from rest_framework import serializers

from engine.apps.products.serializers import ProductListSerializer
from engine.core.tenancy import get_active_store

from .models import Cart, CartItem


class CartItemSerializer(serializers.ModelSerializer):
    product = ProductListSerializer(read_only=True)

    class Meta:
        model = CartItem
        fields = ['public_id', 'product', 'quantity', 'size', 'created_at']


class CartSerializer(serializers.ModelSerializer):
    items = CartItemSerializer(many=True, read_only=True)

    class Meta:
        model = Cart
        fields = ['public_id', 'items', 'created_at', 'updated_at']


class CartAddSerializer(serializers.Serializer):
    # Use public_id (e.g. prd_xxx) — do NOT accept internal UUID/integer PKs
    product_public_id = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1, default=1)
    size = serializers.CharField(max_length=20, allow_blank=True, default='')

    def validate_product_public_id(self, value):
        # Existence and tenant scoping are enforced in the view and return 404.
        request = self.context.get("request")
        ctx = get_active_store(request) if request else None
        if not ctx or not ctx.store:
            raise serializers.ValidationError("Product not found.")
        return value
