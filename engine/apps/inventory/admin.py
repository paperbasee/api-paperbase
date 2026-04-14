from django.contrib import admin

from engine.core.admin import (
    StoreListFilterByInventoryProductStore,
    StoreListFilterByProductStore,
    StoreScopedAdminMixin,
)

from .models import Inventory, StockMovement


class StockMovementInline(admin.TabularInline):
    model = StockMovement
    extra = 0
    readonly_fields = ["change", "reason", "reference", "created_at", "actor"]


@admin.register(Inventory)
class InventoryAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    tenant_store_lookup = "product__store_id"

    list_display = [
        "store_display",
        "product",
        "variant",
        "quantity",
        "low_stock_threshold",
        "is_tracked",
        "updated_at",
    ]
    list_filter = [StoreListFilterByProductStore, "is_tracked"]
    search_fields = ["product__name", "variant__sku"]
    readonly_fields = ["updated_at"]
    inlines = [StockMovementInline]
    autocomplete_fields = ["product", "variant"]

    def optimize_store_queryset(self, qs):
        return qs.select_related("product__store", "variant")

    @admin.display(description="Store")
    def store_display(self, obj):
        return obj.product.store if obj.product_id else None


@admin.register(StockMovement)
class StockMovementAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    tenant_store_lookup = "inventory__product__store_id"

    list_display = [
        "store_display",
        "inventory",
        "change",
        "reason",
        "reference",
        "created_at",
        "actor",
    ]
    list_filter = [StoreListFilterByInventoryProductStore, "reason"]
    readonly_fields = ["inventory", "change", "reason", "reference", "created_at", "actor"]
    date_hierarchy = "created_at"

    def optimize_store_queryset(self, qs):
        return qs.select_related("inventory__product__store", "actor")

    @admin.display(description="Store")
    def store_display(self, obj):
        inv = obj.inventory
        if not inv or not inv.product_id:
            return None
        return inv.product.store
