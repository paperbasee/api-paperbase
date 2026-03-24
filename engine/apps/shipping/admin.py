from django.contrib import admin
from .models import ShippingZone, ShippingMethod, ShippingRate


class ShippingRateInline(admin.TabularInline):
    model = ShippingRate
    extra = 0


@admin.register(ShippingZone)
class ShippingZoneAdmin(admin.ModelAdmin):
    list_display = ['store', 'name', 'is_active']
    list_editable = ['is_active']
    list_filter = ['store', 'is_active']


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    list_display = ['store', 'name', 'method_type', 'is_active', 'order']
    list_editable = ['is_active', 'order']
    filter_horizontal = ['zones']
    inlines = [ShippingRateInline]
    list_filter = ['store', 'is_active', 'method_type']


@admin.register(ShippingRate)
class ShippingRateAdmin(admin.ModelAdmin):
    list_display = ['store', 'shipping_method', 'shipping_zone', 'rate_type', 'price', 'min_order_total', 'max_order_total', 'is_active']
    list_filter = ['store', 'shipping_method', 'shipping_zone']
