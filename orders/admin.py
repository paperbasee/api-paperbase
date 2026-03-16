from django.contrib import admin
from django.utils.html import format_html

from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ('product_name', 'quantity', 'price')
    readonly_fields = ('product_name',)

    @admin.display(description='Product name')
    def product_name(self, obj: OrderItem):
        return getattr(obj.product, 'name', '') or str(obj.product_id)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        'product_names', 'shipping_name', 'phone', 'district',
        'delivery_area', 'status', 'total', 'created_at',
    ]
    list_filter = ['status', 'delivery_area', 'created_at']
    list_editable = ['status']
    inlines = [OrderItemInline]
    readonly_fields = (
        'id', 'order_number', 'email', 'status', 'total', 'shipping_name',
        'shipping_address', 'phone', 'delivery_area', 'district',
        'tracking_number', 'created_at', 'updated_at',
    )
    exclude = ('user',)

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
