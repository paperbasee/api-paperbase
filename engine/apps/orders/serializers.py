from rest_framework import serializers

from engine.apps.products.serializers import ProductListSerializer

from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    product = ProductListSerializer(read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'quantity', 'price']


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    delivery_area_label = serializers.CharField(source='get_delivery_area_display', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'status', 'subtotal', 'shipping_cost', 'total',
            'shipping_zone', 'shipping_method',
            'shipping_name', 'shipping_address',
            'phone', 'email', 'district', 'delivery_area', 'delivery_area_label',
            'tracking_number', 'created_at', 'updated_at', 'items',
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Expose order_number as id for customer-facing API
        data['id'] = instance.order_number or str(instance.id)
        return data


class OrderCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    shipping_name = serializers.CharField(max_length=255)
    shipping_address = serializers.CharField()

    def validate_shipping_name(self, value):
        if not (value or '').strip():
            raise serializers.ValidationError('Required.')
        return value.strip()

    def validate_shipping_address(self, value):
        if not (value or '').strip():
            raise serializers.ValidationError('Required.')
        return value.strip()


class DirectOrderCreateSerializer(serializers.Serializer):
    """Serializer for creating orders directly with products (not from cart)."""
    shipping_name = serializers.CharField(max_length=255)
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    shipping_address = serializers.CharField()
    district = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    delivery_area = serializers.ChoiceField(choices=['inside', 'outside'])
    products = serializers.ListField(
        child=serializers.DictField(),
        min_length=1
    )

    def validate_shipping_name(self, value):
        if not (value or '').strip():
            raise serializers.ValidationError('Required.')
        return value.strip()

    def validate_phone(self, value):
        raw = (value or '').strip()
        if not raw:
            raise serializers.ValidationError('Required.')
        digits = ''.join(c for c in raw if c.isdigit())
        if len(digits) != 11 or not digits.startswith('01'):
            raise serializers.ValidationError(
                'Phone must be 11 digits, start with 01, and contain only numbers.'
            )
        return digits

    def validate_shipping_address(self, value):
        if not (value or '').strip():
            raise serializers.ValidationError('Required.')
        return value.strip()

    def validate_products(self, value):
        if not value:
            raise serializers.ValidationError('At least one product is required.')
        for product in value:
            if 'id' not in product or 'quantity' not in product:
                raise serializers.ValidationError('Each product must have id and quantity.')
        return value
