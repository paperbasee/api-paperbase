from django.contrib import admin

from engine.core.admin import StoreListFilter, StoreScopedAdminMixin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = ["store", "phone", "name", "email", "created_at"]
    list_filter = (StoreListFilter,)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "store",
                    "phone",
                    "name",
                    "email",
                    "address",
                    "total_orders",
                    "total_spent",
                    "last_order_at",
                ),
            },
        ),
    )

    def optimize_store_queryset(self, qs):
        return qs.select_related("store")
