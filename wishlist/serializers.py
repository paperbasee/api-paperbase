from rest_framework import serializers

from products.serializers import ProductListSerializer
from products.models import Product

from .models import WishlistItem


class WishlistItemSerializer(serializers.ModelSerializer):
    product = ProductListSerializer(read_only=True)

    class Meta:
        model = WishlistItem
        fields = ['id', 'product', 'created_at']


class WishlistAddSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()

    def validate_product_id(self, value):
        if not Product.objects.filter(id=value, is_active=True).exists():
            raise serializers.ValidationError('Product not found.')
        return value
