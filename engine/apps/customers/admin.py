from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["phone", "name", "email", "created_at"]
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
