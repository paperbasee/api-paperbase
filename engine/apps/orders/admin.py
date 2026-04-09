from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.html import format_html

from decimal import Decimal

from .stock import adjust_stock
from engine.apps.orders.order_financials import money
from engine.apps.orders.services import write_order_item_financials, recalculate_order_totals

from .models import Order, OrderItem, OrderAddress


class OrderAddressInline(admin.TabularInline):
    model = OrderAddress
    extra = 0


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    autocomplete_fields = ("product", "variant")
    fields = (
        "product",
        "variant",
        "product_name_snapshot",
        "variant_snapshot",
        "unit_price_snapshot",
        "quantity",
        "unit_price",
        "original_price",
        "discount_amount",
        "line_subtotal",
        "line_total",
    )
    readonly_fields = (
        "product_name_snapshot",
        "variant_snapshot",
        "unit_price_snapshot",
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        'product_names', 'shipping_name', 'phone', 'district',
        'status', 'total', 'created_at',
    ]
    list_filter = ['status', 'created_at']
    inlines = [OrderItemInline, OrderAddressInline]
    readonly_fields = (
        "id",
        "order_number",
        "created_at",
        "updated_at",
        "pricing_snapshot",
    )
    exclude = ('user',)
    fieldsets = (
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
                ),
            },
        ),
        (
            "Pricing snapshot",
            {
                "fields": ("pricing_snapshot",),
                "classes": ("collapse",),
            },
        ),
    )

    def has_delete_permission(self, request, obj=None):
        return bool(getattr(request.user, "is_superuser", False))

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not getattr(request.user, "is_superuser", False):
            actions.pop("delete_selected", None)
        return actions

    def save_formset(self, request, form, formset, change):
        """
        When editing order items in Django admin, adjust stock based on quantity deltas.
        """
        if formset.model is not OrderItem:
            return super().save_formset(request, form, formset, change)

        original: dict[int, tuple[str, int | None, int]] = {}
        if change and form.instance and getattr(form.instance, "pk", None):
            for oi in OrderItem.objects.filter(order=form.instance).only(
                "id", "product_id", "variant_id", "quantity"
            ):
                original[oi.id] = (str(oi.product_id), oi.variant_id, int(oi.quantity))

        with transaction.atomic():
            instances = formset.save(commit=False)
            deleted = list(formset.deleted_objects)

            for obj in deleted:
                try:
                    adjust_stock(
                        store_id=form.instance.store_id,
                        product_id=obj.product_id,
                        variant_id=obj.variant_id,
                        delta_qty=-int(obj.quantity),
                    )
                except ValidationError as e:
                    raise ValidationError(e)
                obj.delete()

            for obj in instances:
                prev = original.get(getattr(obj, "id", None))
                prev_product_id, prev_variant_id, prev_qty = (None, None, 0)
                if prev is not None:
                    prev_product_id, prev_variant_id, prev_qty = prev

                new_qty = int(obj.quantity or 0)
                if not obj.product_id:
                    z = Decimal("0.00")
                    obj.unit_price = obj.unit_price or z
                    obj.original_price = obj.original_price or z
                    obj.discount_amount = obj.discount_amount or z
                    obj.line_subtotal = z
                    obj.line_total = z
                    obj.save()
                    continue

                new_product_id = str(obj.product_id)
                new_variant_id = obj.variant_id

                if prev is not None and (
                    str(prev_product_id) != new_product_id or prev_variant_id != new_variant_id
                ):
                    adjust_stock(
                        store_id=form.instance.store_id,
                        product_id=prev_product_id,
                        variant_id=prev_variant_id,
                        delta_qty=-prev_qty,
                    )
                    adjust_stock(
                        store_id=form.instance.store_id,
                        product_id=new_product_id,
                        variant_id=new_variant_id,
                        delta_qty=new_qty,
                    )
                else:
                    delta = new_qty - int(prev_qty or 0)
                    if delta != 0:
                        adjust_stock(
                            store_id=form.instance.store_id,
                            product_id=new_product_id,
                            variant_id=new_variant_id,
                            delta_qty=delta,
                        )

                write_order_item_financials(
                    obj,
                    product=obj.product,
                    variant=obj.variant,
                    quantity=new_qty,
                    unit_price=money(obj.unit_price or Decimal("0.00")),
                )
                obj.save()

            formset.save_m2m()
            if form.instance and getattr(form.instance, "pk", None):
                recalculate_order_totals(form.instance)

    @admin.display(description='Products')
    def product_names(self, obj: Order):
        names = [
            oi.product.name if oi.product else "Unavailable"
            for oi in obj.items.select_related('product').all()
        ]
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
