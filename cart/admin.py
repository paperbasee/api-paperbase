from django.contrib import admin

from .models import CartItem


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ['product_name', 'quantity', 'size', 'created_at']
    list_filter = ['created_at']

    @admin.display(description='Product')
    def product_name(self, obj):
        return obj.product.name

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return True

    def has_delete_permission(self, request, obj=None):
        return False
