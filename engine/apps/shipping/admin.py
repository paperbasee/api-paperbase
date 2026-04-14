from django.contrib import admin

from engine.core.admin import StoreListFilter, StoreScopedAdminMixin

from .models import ShippingMethod, ShippingRate, ShippingZone


class ShippingRateInline(admin.TabularInline):
    model = ShippingRate
    extra = 0


@admin.register(ShippingZone)
class ShippingZoneAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = ["store", "name", "is_active"]
    list_editable = ["is_active"]
    list_filter = [StoreListFilter, "is_active"]

    def optimize_store_queryset(self, qs):
        return qs.select_related("store")


@admin.register(ShippingMethod)
class ShippingMethodAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = ["store", "name", "method_type", "is_active", "order"]
    list_editable = ["is_active", "order"]
    filter_horizontal = ["zones"]
    inlines = [ShippingRateInline]
    list_filter = [StoreListFilter, "is_active", "method_type"]

    def optimize_store_queryset(self, qs):
        return qs.select_related("store")


@admin.register(ShippingRate)
class ShippingRateAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "store",
        "shipping_method",
        "shipping_zone",
        "rate_type",
        "price",
        "min_order_total",
        "max_order_total",
        "is_active",
    ]
    list_filter = [StoreListFilter, "shipping_method", "shipping_zone"]

    def optimize_store_queryset(self, qs):
        return qs.select_related("store", "shipping_method", "shipping_zone")
