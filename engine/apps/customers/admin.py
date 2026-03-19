from django.contrib import admin

from engine.apps.stores.models import Store

from .admin_forms import CustomerAdminForm, build_customer_extra_form_fields
from .extra_schema import form_field_name_for_schema_item, get_customer_extra_schema
from .models import Customer, CustomerAddress


class CustomerAddressInline(admin.TabularInline):
    model = CustomerAddress
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['user', 'phone', 'marketing_opt_in', 'created_at']
    inlines = [CustomerAddressInline]
    raw_id_fields = ['user', 'default_shipping_address', 'default_billing_address']
    form = CustomerAdminForm

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
        return get_customer_extra_schema(store) if store else []

    def get_form(self, request, obj=None, change=False, **kwargs):
        schema = self._extra_schema(request, obj=obj)
        extra_fields = build_customer_extra_form_fields(schema)

        base_form = kwargs.pop("form", None) or self.form
        if extra_fields:
            dynamic_form = type(
                "DynamicCustomerAdminForm",
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
                        "user",
                        "phone",
                        "marketing_opt_in",
                        "default_shipping_address",
                        "default_billing_address",
                    ),
                    "description": (
                        "Choose Store first when adding a customer. Custom fields from that store’s "
                        "dashboard schema show after you save or if the form reloads with errors."
                    ),
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
                            "dashboard). Values are saved on the customer’s extra_data JSON field."
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
                            "No customer custom fields are configured for the selected store yet. "
                            "Add them in the dashboard under Settings → Dynamic Fields, or edit JSON here."
                        ),
                    },
                )
            )

        return base


@admin.register(CustomerAddress)
class CustomerAddressAdmin(admin.ModelAdmin):
    list_display = ['customer', 'label', 'name', 'city', 'country', 'is_default_shipping', 'is_default_billing']
    list_filter = ['customer']
