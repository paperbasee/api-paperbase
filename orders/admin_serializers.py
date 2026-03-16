from rest_framework import serializers

from .models import Order, OrderItem


class AdminOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_brand = serializers.CharField(source='product.brand', read_only=True)
    product_image = serializers.SerializerMethodField()
    original_price = serializers.DecimalField(
        source='product.original_price',
        max_digits=10,
        decimal_places=2,
        allow_null=True,
        read_only=True,
    )

    class Meta:
        model = OrderItem
        fields = [
            'id', 'product', 'product_name', 'product_brand', 'product_image',
            'quantity', 'size', 'price', 'original_price',
        ]
        read_only_fields = ['id']

    def get_product_image(self, obj):
        if obj.product.image and hasattr(obj.product.image, 'url'):
            return obj.product.image.url
        return None


class AdminOrderListSerializer(serializers.ModelSerializer):
    items_count = serializers.SerializerMethodField()
    delivery_area_label = serializers.CharField(
        source='get_delivery_area_display', read_only=True,
    )

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'email', 'status', 'total',
            'shipping_name', 'phone', 'district', 'delivery_area',
            'delivery_area_label', 'items_count', 'created_at', 'updated_at',
        ]

    def get_items_count(self, obj):
        return obj.items.count()


class AdminOrderSerializer(serializers.ModelSerializer):
    items = AdminOrderItemSerializer(many=True, read_only=True)
    delivery_area_label = serializers.CharField(
        source='get_delivery_area_display', read_only=True,
    )

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'user', 'email', 'status', 'total',
            'shipping_name', 'shipping_address', 'phone',
            'delivery_area', 'delivery_area_label', 'district',
            'tracking_number', 'items', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'order_number', 'total', 'created_at', 'updated_at',
        ]


class AdminOrderStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.Status.choices)
