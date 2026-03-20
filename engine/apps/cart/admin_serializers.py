from rest_framework import serializers

from .models import Cart, CartItem


class AdminCartItemSerializer(serializers.ModelSerializer):
    product_public_id = serializers.CharField(source='product.public_id', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_brand = serializers.CharField(source='product.brand', read_only=True)

    class Meta:
        model = CartItem
        fields = [
            'public_id', 'product_public_id', 'product_name', 'product_brand', 'quantity', 'size',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['public_id', 'product_public_id', 'product_name', 'product_brand', 'created_at', 'updated_at']


class AdminCartSerializer(serializers.ModelSerializer):
    items = AdminCartItemSerializer(many=True, read_only=True)

    class Meta:
        model = Cart
        fields = [
            'public_id', 'items', 'created_at', 'updated_at',
        ]
        read_only_fields = ['public_id', 'created_at', 'updated_at']
