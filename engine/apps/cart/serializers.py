from rest_framework import serializers

from engine.apps.products.serializers import ProductListSerializer

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
        from engine.apps.products.models import Product
        if not Product.objects.filter(public_id=value, is_active=True).exists():
            raise serializers.ValidationError('Product not found.')
        return value
