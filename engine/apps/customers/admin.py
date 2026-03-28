from django.contrib import admin

from .models import Customer, CustomerAddress


class CustomerAddressInline(admin.TabularInline):
    model = CustomerAddress
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['user', 'phone', 'marketing_opt_in', 'created_at']
    inlines = [CustomerAddressInline]
    raw_id_fields = ['user', 'default_shipping_address', 'default_billing_address']
    fieldsets = (
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
            },
        ),
    )


@admin.register(CustomerAddress)
class CustomerAddressAdmin(admin.ModelAdmin):
    list_display = ['customer', 'label', 'name', 'city', 'country', 'is_default_shipping', 'is_default_billing']
    list_filter = ['customer']
