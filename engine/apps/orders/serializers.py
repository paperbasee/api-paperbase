from rest_framework import serializers

from engine.apps.products.serializers import ProductListSerializer
from engine.apps.shipping.models import ShippingMethod, ShippingZone

from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    product = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = ['public_id', 'product', 'product_name', 'status', 'quantity', 'price']
        read_only_fields = ['public_id']

    def get_product(self, obj):
        if not obj.product:
            return None
        return ProductListSerializer(obj.product, context=self.context).data

    def get_product_name(self, obj):
        return obj.product.name if obj.product else "Unavailable"

    def get_status(self, obj):
        return "active" if obj.product else "deleted"


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    shipping_zone_public_id = serializers.CharField(source='shipping_zone.public_id', read_only=True, allow_null=True)
    shipping_method_public_id = serializers.CharField(source='shipping_method.public_id', read_only=True, allow_null=True)
    customer = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'public_id', 'order_number', 'status', 'subtotal', 'shipping_cost', 'total',
            'shipping_zone_public_id', 'shipping_method_public_id',
            'shipping_name', 'shipping_address',
            'phone', 'email', 'district',
            'tracking_number', 'customer', 'created_at', 'updated_at', 'items',
        ]
        read_only_fields = ['public_id', 'order_number']

    def get_customer(self, obj):
        customer = getattr(obj, "customer", None)
        if not customer:
            return None
        return {
            "public_id": customer.public_id,
            "name": customer.name,
            "phone": customer.phone,
        }


class OrderCreateSerializer(serializers.Serializer):
    shipping_zone = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingZone.objects.none(),
    )
    shipping_method = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingMethod.objects.none(),
        allow_null=True,
        required=False,
    )
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    shipping_name = serializers.CharField(max_length=255)
    shipping_address = serializers.CharField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        store = self.context.get("store")
        if not store:
            return
        self.fields["shipping_zone"].queryset = ShippingZone.objects.filter(store=store, is_active=True)
        self.fields["shipping_method"].queryset = ShippingMethod.objects.filter(store=store, is_active=True)

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
    shipping_zone = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingZone.objects.none(),
    )
    shipping_method = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingMethod.objects.none(),
        allow_null=True,
        required=False,
    )
    shipping_name = serializers.CharField(max_length=255)
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    shipping_address = serializers.CharField()
    district = serializers.CharField(max_length=100, required=False, allow_blank=True, default='')
    products = serializers.ListField(
        child=serializers.DictField(),
        min_length=1
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        store = self.context.get("store")
        if not store:
            return
        self.fields["shipping_zone"].queryset = ShippingZone.objects.filter(store=store, is_active=True)
        self.fields["shipping_method"].queryset = ShippingMethod.objects.filter(store=store, is_active=True)

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
            if 'public_id' not in product or 'quantity' not in product:
                raise serializers.ValidationError('Each product must have public_id and quantity.')
        return value
