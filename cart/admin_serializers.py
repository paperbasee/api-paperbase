from rest_framework import serializers

from .models import Cart, CartItem


class AdminCartItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_brand = serializers.CharField(source='product.brand', read_only=True)

    class Meta:
        model = CartItem
        fields = [
            'id', 'product', 'product_name', 'product_brand', 'quantity', 'size',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class AdminCartSerializer(serializers.ModelSerializer):
    items = AdminCartItemSerializer(many=True, read_only=True)

    class Meta:
        model = Cart
        fields = [
            'id', 'items', 'created_at', 'updated_at',
        ]
        read_only_fields = fields
