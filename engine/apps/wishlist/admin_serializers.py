from rest_framework import serializers

from .models import WishlistItem


class AdminWishlistItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_brand = serializers.CharField(source='product.brand', read_only=True)
    product_public_id = serializers.CharField(source='product.public_id', read_only=True)

    class Meta:
        model = WishlistItem
        fields = [
            'public_id', 'product_public_id', 'product_name', 'product_brand', 'created_at',
        ]
        read_only_fields = ['public_id', 'product_name', 'product_brand', 'product_public_id', 'created_at']
