from rest_framework import serializers

from engine.apps.products.serializers import ProductListSerializer
from engine.apps.products.models import Product

from .models import WishlistItem


class WishlistItemSerializer(serializers.ModelSerializer):
    product = ProductListSerializer(read_only=True)

    class Meta:
        model = WishlistItem
        fields = ['public_id', 'product', 'created_at']
        read_only_fields = ['public_id']


class WishlistAddSerializer(serializers.Serializer):
    # Use public_id (e.g. prd_xxx) — do NOT accept internal UUID/integer PKs
    product_public_id = serializers.CharField()

    def validate_product_public_id(self, value):
        if not Product.objects.filter(public_id=value, is_active=True).exists():
            raise serializers.ValidationError('Product not found.')
        return value
