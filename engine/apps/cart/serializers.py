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
    product_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, default=1)
    size = serializers.CharField(max_length=20, allow_blank=True, default='')

    def validate_product_id(self, value):
        from engine.apps.products.models import Product
        if not Product.objects.filter(id=value, is_active=True).exists():
            raise serializers.ValidationError('Product not found.')
        return value
