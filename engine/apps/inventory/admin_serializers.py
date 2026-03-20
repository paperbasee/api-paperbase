from rest_framework import serializers
from .models import Inventory, StockMovement


class StockMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockMovement
        fields = ['id', 'public_id', 'change', 'reason', 'reference', 'created_at', 'actor']
        read_only_fields = fields


class InventoryListSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    variant_sku = serializers.CharField(source='variant.sku', read_only=True, allow_null=True)
    is_low = serializers.SerializerMethodField()

    class Meta:
        model = Inventory
        fields = [
            'public_id', 'product', 'product_name', 'variant', 'variant_sku',
            'quantity', 'low_stock_threshold', 'is_tracked', 'updated_at', 'is_low',
        ]

    def get_is_low(self, obj):
        return obj.is_low_stock()


class InventoryDetailSerializer(InventoryListSerializer):
    movements = StockMovementSerializer(many=True, read_only=True)

    class Meta(InventoryListSerializer.Meta):
        fields = InventoryListSerializer.Meta.fields + ['movements']
