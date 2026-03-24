from rest_framework import serializers

from engine.apps.products.serializers import ProductListSerializer
from engine.core.tenancy import get_active_store

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
        request = self.context.get("request")
        ctx = get_active_store(request) if request else None
        if not ctx or not ctx.store:
            raise serializers.ValidationError("Product not found.")
        return value
