from django.contrib import admin
from django.core.exceptions import ValidationError
from django.utils.html import format_html

from engine.apps.stores.models import Store

from .admin_forms import OrderAdminForm, build_order_extra_form_fields
from .extra_schema import form_field_name_for_schema_item, get_order_extra_schema
from .stock import adjust_stock

from .models import Order, OrderItem, OrderAddress, OrderStatusHistory


class OrderAddressInline(admin.TabularInline):
    model = OrderAddress
    extra = 0


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = ['status', 'note', 'created_at']


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    autocomplete_fields = ("product", "variant")
    fields = ("product", "variant", "quantity", "price")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    form = OrderAdminForm
    list_display = [
        'product_names', 'shipping_name', 'phone', 'district',
        'delivery_area', 'status', 'total', 'created_at',
    ]
    list_filter = ['status', 'created_at']
    list_editable = ['status']
    inlines = [OrderItemInline, OrderAddressInline, OrderStatusHistoryInline]
    # Allow editing core order fields in admin. Keep identity/timestamps read-only.
    readonly_fields = (
        "id",
        "order_number",
        "created_at",
        "updated_at",
    )
    exclude = ('user',)

    def _resolve_store(self, request, obj=None) -> Store | None:
        if obj and getattr(obj, "pk", None) and getattr(obj, "store_id", None):
            return obj.store
        if request.method == "POST" and request.POST.get("store"):
            try:
                return Store.objects.get(pk=request.POST["store"])
            except (Store.DoesNotExist, ValueError, TypeError):
                return None
        if request.method == "GET" and request.GET.get("store__id__exact"):
            try:
                return Store.objects.get(pk=request.GET["store__id__exact"])
            except (Store.DoesNotExist, ValueError, TypeError):
                return None
        return None

    def _extra_schema(self, request, obj=None) -> list[dict]:
        store = self._resolve_store(request, obj=obj)
        return get_order_extra_schema(store) if store else []

    def get_form(self, request, obj=None, change=False, **kwargs):
        """
        Provide a dynamic form class that includes extra_schema_* fields at class-definition time.
        """
        schema = self._extra_schema(request, obj=obj)
        extra_fields = build_order_extra_form_fields(schema)

        base_form = kwargs.pop("form", None) or self.form
        if extra_fields:
            dynamic_form = type(
                "DynamicOrderAdminForm",
                (base_form,),
                {**extra_fields},
            )
            kwargs["form"] = dynamic_form
        else:
            kwargs["form"] = base_form

        return super().get_form(request, obj=obj, change=change, **kwargs)

    def get_fieldsets(self, request, obj=None):
        base = [
            (
                None,
                {
                    "fields": (
                        "store",
                        "order_number",
                        "email",
                        "status",
                        "total",
                        "created_at",
                        "updated_at",
                    ),
                    "description": (
                        "Choose Store first when adding an order. Custom fields from that store’s "
                        "dashboard schema show after you save or if the form reloads with errors."
                    ),
                },
            ),
            (
                "Shipping",
                {
                    "fields": (
                        "shipping_name",
                        "phone",
                        "shipping_address",
                        "district",
                        "delivery_area",
                        "tracking_number",
                    )
                },
            ),
        ]

        schema = self._extra_schema(request, obj=obj)
        extra_names = [
            form_field_name_for_schema_item(str(it.get("id") or it.get("name") or ""))
            for it in schema
            if (it.get("name") or "").strip()
        ]

        if extra_names:
            base.append(
                (
                    "Custom fields (dashboard schema)",
                    {
                        "fields": tuple(extra_names),
                        "description": (
                            "Defined in Store settings → extra_field_schema (same as the merchant "
                            "dashboard). Values are saved on the order’s extra_data JSON field."
                        ),
                    },
                )
            )
        else:
            base.append(
                (
                    "Extra data (JSON)",
                    {
                        "fields": ("extra_data",),
                        "classes": ("collapse",),
                        "description": (
                            "No order custom fields are configured for the selected store yet. "
                            "Add them in the dashboard under Settings → Dynamic Fields, or edit JSON here."
                        ),
                    },
                )
            )

        return base

    def save_formset(self, request, form, formset, change):
        """
        When editing order items in Django admin, adjust stock based on quantity deltas.
        """
        if formset.model is not OrderItem:
            return super().save_formset(request, form, formset, change)

        # Snapshot original quantities for existing items.
        original: dict[int, tuple[str, int | None, int]] = {}
        if change and form.instance and getattr(form.instance, "pk", None):
            for oi in OrderItem.objects.filter(order=form.instance).only(
                "id", "product_id", "variant_id", "quantity"
            ):
                original[oi.id] = (str(oi.product_id), oi.variant_id, int(oi.quantity))

        instances = formset.save(commit=False)
        deleted = list(formset.deleted_objects)

        # Handle deletions first (restore stock).
        for obj in deleted:
            try:
                adjust_stock(
                    product_id=obj.product_id,
                    variant_id=obj.variant_id,
                    delta_qty=-int(obj.quantity),
                )
            except ValidationError as e:
                raise ValidationError(e)
            obj.delete()

        # Handle creates/updates (reduce or restore by delta).
        for obj in instances:
            prev = original.get(getattr(obj, "id", None))
            prev_product_id, prev_variant_id, prev_qty = (None, None, 0)
            if prev is not None:
                prev_product_id, prev_variant_id, prev_qty = prev

            new_product_id = str(obj.product_id)
            new_variant_id = obj.variant_id
            new_qty = int(obj.quantity or 0)

            # If target changed, restore old then reduce new.
            if prev is not None and (
                str(prev_product_id) != new_product_id or prev_variant_id != new_variant_id
            ):
                adjust_stock(
                    product_id=prev_product_id,
                    variant_id=prev_variant_id,
                    delta_qty=-prev_qty,
                )
                adjust_stock(
                    product_id=new_product_id,
                    variant_id=new_variant_id,
                    delta_qty=new_qty,
                )
            else:
                delta = new_qty - int(prev_qty or 0)
                if delta != 0:
                    adjust_stock(
                        product_id=new_product_id,
                        variant_id=new_variant_id,
                        delta_qty=delta,
                    )

            obj.save()

        formset.save_m2m()

    @admin.display(description='Products')
    def product_names(self, obj: Order):
        names = [oi.product.name for oi in obj.items.select_related('product').all()]
        if not names:
            return ''
        if len(names) <= 3:
            text = ', '.join(names)
        else:
            text = ', '.join(names[:3]) + f' (+{len(names) - 3} more)'
        return format_html(
            '<span title="{}" style="display:block;max-width:300px;'
            'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{}</span>',
            text, text,
        )
