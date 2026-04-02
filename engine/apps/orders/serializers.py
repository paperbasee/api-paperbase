from rest_framework import serializers

from engine.core.serializers import SafeModelSerializer
from engine.apps.shipping.models import ShippingMethod, ShippingZone

from .models import Order, OrderItem


def storefront_order_line_variant_details(line: OrderItem) -> str | None:
    """Human-readable variant options for storefront receipts, e.g. 'Size: XL, Color: Red'."""
    if not line.variant_id:
        return None
    variant = getattr(line, "variant", None)
    if variant is None:
        return None
    rows = []
    for link in variant.attribute_values.select_related("attribute_value__attribute").all():
        av = link.attribute_value
        attr = av.attribute
        rows.append((attr.order, attr.slug or "", attr.name, av.value))
    if not rows:
        return None
    rows.sort(key=lambda t: (t[0], t[1], t[2]))
    return ", ".join(f"{name}: {value}" for _, _, name, value in rows)


class OrderItemSerializer(SafeModelSerializer):
    product_public_id = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    is_unavailable = serializers.SerializerMethodField()
    variant_public_id = serializers.SerializerMethodField()
    variant_sku = serializers.SerializerMethodField()
    variant_options = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            'public_id',
            'product_public_id',
            'product_name',
            'status',
            'is_unavailable',
            'quantity',
            'unit_price',
            'original_price',
            'discount_amount',
            'line_subtotal',
            'line_total',
            'variant_public_id',
            'variant_sku',
            'variant_options',
        ]
        read_only_fields = ['public_id']

    def get_product_public_id(self, obj):
        return obj.product.public_id if obj.product else None

    def get_product_name(self, obj):
        return obj.product.name if obj.product else "Unavailable"

    def get_status(self, obj):
        return "active" if obj.product else "deleted"

    def get_is_unavailable(self, obj):
        return obj.product is None

    def get_variant_public_id(self, obj):
        return obj.variant.public_id if obj.variant_id else None

    def get_variant_sku(self, obj):
        return (obj.variant.sku or "") if obj.variant_id else None

    def get_variant_options(self, obj):
        if not obj.variant_id:
            return None
        rows = []
        for link in obj.variant.attribute_values.select_related("attribute_value__attribute").all():
            av = link.attribute_value
            attr = av.attribute
            rows.append(
                {
                    "attribute_public_id": attr.public_id,
                    "attribute_slug": attr.slug,
                    "attribute_name": attr.name,
                    "value_public_id": av.public_id,
                    "value": av.value,
                }
            )
        return rows


class OrderSerializer(SafeModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    shipping_zone_public_id = serializers.CharField(source='shipping_zone.public_id', read_only=True, allow_null=True)
    shipping_method_public_id = serializers.CharField(source='shipping_method.public_id', read_only=True, allow_null=True)
    shipping_rate_public_id = serializers.CharField(
        source='shipping_rate.public_id', read_only=True, allow_null=True
    )
    customer = serializers.SerializerMethodField()
    has_unavailable_products = serializers.SerializerMethodField()
    unavailable_products_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'public_id', 'order_number', 'status',
            'subtotal_before_discount', 'discount_total', 'subtotal_after_discount',
            'shipping_cost', 'total',
            'pricing_snapshot',
            'shipping_zone_public_id', 'shipping_method_public_id', 'shipping_rate_public_id',
            'shipping_name', 'shipping_address',
            'phone', 'email', 'district',
            'courier_provider', 'courier_consignment_id',
            'sent_to_courier', 'customer_confirmation_sent_at',
            'customer', 'created_at', 'updated_at', 'items',
            'has_unavailable_products', 'unavailable_products_count',
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

    def get_unavailable_products_count(self, obj):
        return obj.items.filter(product__isnull=True).count()

    def get_has_unavailable_products(self, obj):
        return self.get_unavailable_products_count(obj) > 0


class StorefrontOrderLineReceiptSerializer(serializers.BaseSerializer):
    """Single line on a storefront receipt (no model field leakage)."""

    def to_representation(self, line: OrderItem) -> dict:
        product_name = line.product.name if line.product else "Unavailable"
        return {
            "product_name": product_name,
            "quantity": line.quantity,
            "unit_price": str(line.unit_price),
            "total_price": str(line.line_total),
            "variant_details": storefront_order_line_variant_details(line),
        }


class StorefrontOrderReceiptSerializer(serializers.BaseSerializer):
    """Minimal storefront checkout receipt (POST /api/v1/orders/ only)."""

    def to_representation(self, order: Order) -> dict:
        line_ser = StorefrontOrderLineReceiptSerializer()
        items_out = [line_ser.to_representation(line) for line in order.items.all()]
        return {
            "public_id": order.public_id,
            "order_number": order.order_number,
            "status": order.status,
            "customer_name": order.shipping_name,
            "phone": order.phone,
            "shipping_address": order.shipping_address,
            "items": items_out,
            "subtotal": str(order.subtotal_after_discount),
            "shipping_cost": str(order.shipping_cost),
            "total": str(order.total),
        }


class OrderCreateSerializer(serializers.Serializer):
    """Stateless checkout: shipping fields + line items from the request body."""
    shipping_zone_public_id = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingZone.objects.none(),
        source='shipping_zone',
    )
    shipping_method_public_id = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingMethod.objects.none(),
        allow_null=True,
        required=False,
        source='shipping_method',
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
    max_quantity_per_item = 1000

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        store = self.context.get("store")
        if not store:
            return
        self.fields["shipping_zone_public_id"].queryset = ShippingZone.objects.filter(
            store=store, is_active=True
        )
        self.fields["shipping_method_public_id"].queryset = ShippingMethod.objects.filter(
            store=store, is_active=True
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
        from engine.apps.products.models import Product
        from engine.apps.products.variant_utils import resolve_storefront_variant

        if not value:
            raise serializers.ValidationError('At least one product is required.')
        store = self.context.get("store")
        if not store:
            raise serializers.ValidationError("Store context is required.")
        for product in value:
            allowed_keys = {"product_public_id", "quantity", "variant_public_id"}
            unknown_keys = set(product.keys()) - allowed_keys
            if unknown_keys:
                raise serializers.ValidationError(
                    f"Unknown product fields are not allowed: {', '.join(sorted(unknown_keys))}."
                )
            if 'product_public_id' not in product or 'quantity' not in product:
                raise serializers.ValidationError(
                    'Each product must have product_public_id and quantity.'
                )
            public_id = str(product.get("product_public_id", "")).strip()
            if not public_id.startswith("prd_"):
                raise serializers.ValidationError("Invalid product public_id.")
            quantity = product.get("quantity")
            if not isinstance(quantity, int):
                raise serializers.ValidationError("Quantity must be an integer.")
            if quantity <= 0:
                raise serializers.ValidationError("Quantity must be greater than zero.")
            if quantity > self.max_quantity_per_item:
                raise serializers.ValidationError(
                    f"Quantity cannot exceed {self.max_quantity_per_item} per item."
                )
            p_obj = (
                Product.objects.filter(
                    public_id=public_id,
                    store=store,
                    is_active=True,
                    status=Product.Status.ACTIVE,
                )
                .first()
            )
            if not p_obj:
                raise serializers.ValidationError(f"Product {public_id} not found or unavailable.")
            resolve_storefront_variant(
                product=p_obj,
                variant_public_id=product.get("variant_public_id"),
            )
        return value
