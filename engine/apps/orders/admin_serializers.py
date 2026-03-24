from decimal import Decimal

from rest_framework import serializers

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from engine.apps.products.models import Product, ProductVariant
from engine.apps.orders.stock import adjust_stock
from engine.apps.shipping.models import ShippingMethod, ShippingZone
from engine.apps.shipping.service import quote_shipping
from engine.apps.orders.services import resolve_and_attach_customer

from .models import Order, OrderItem


class StoreScopedProductSlugRelatedField(serializers.SlugRelatedField):
    """
    Resolve product by public_id scoped to context['active_store'].
    Nested list item serializers may run before a static queryset is valid; using
    get_queryset() defers filtering until validation when root context is available.
    """

    def get_queryset(self):
        ctx = self.context
        active_store = ctx.get("active_store") if isinstance(ctx, dict) else None
        if not active_store:
            return Product.objects.none()
        return Product.objects.filter(store=active_store)


def _shipping_cost_for_order(order: Order, *, order_subtotal: Decimal) -> Decimal:
    quote = quote_shipping(
        store=order.store,
        order_subtotal=order_subtotal,
        delivery_area=(order.delivery_area or "").strip().lower() or None,
        district=(order.district or "").strip() or None,
    )
    return quote.shipping_cost


class AdminOrderItemSerializer(serializers.ModelSerializer):
    # Expose public_id only — do NOT expose product UUID/integer PK
    product = serializers.SlugRelatedField(slug_field='public_id', read_only=True)
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
    variant_public_id = serializers.CharField(source="variant.public_id", read_only=True, allow_null=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True, allow_null=True)
    variant_stock_quantity = serializers.IntegerField(source="variant.stock_quantity", read_only=True, allow_null=True)
    variant_option_labels = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            'public_id', 'product', 'product_name', 'product_brand', 'product_image',
            'variant_public_id', 'variant_sku', 'variant_stock_quantity', 'variant_option_labels',
            'quantity', 'price', 'original_price',
        ]
        read_only_fields = ['public_id']

    def get_product_image(self, obj):
        if obj.product.image and hasattr(obj.product.image, 'url'):
            return obj.product.image.url
        return None

    def get_variant_option_labels(self, obj):
        v = getattr(obj, "variant", None)
        if not v:
            return []
        links = (
            v.attribute_values.select_related("attribute_value__attribute")
            .order_by("attribute_value__attribute__order", "attribute_value__order")
            .all()
        )
        return [
            f"{link.attribute_value.attribute.name}: {link.attribute_value.value}"
            for link in links
        ]


class AdminOrderListSerializer(serializers.ModelSerializer):
    items_count = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()
    delivery_area_label = serializers.CharField(
        source='get_delivery_area_display', read_only=True,
    )

    class Meta:
        model = Order
        fields = [
            'public_id', 'order_number', 'email', 'status', 'subtotal', 'shipping_cost', 'total',
            'shipping_name', 'phone', 'district', 'delivery_area',
            'delivery_area_label', 'items_count', 'customer', 'extra_data',
            'courier_provider', 'courier_consignment_id', 'courier_tracking_code',
            'courier_status', 'sent_to_courier', 'customer_confirmation_sent_at',
            'created_at', 'updated_at',
        ]

    def get_items_count(self, obj):
        return obj.items.count()

    def get_customer(self, obj):
        customer = getattr(obj, "customer", None)
        if not customer:
            return None
        return {"public_id": customer.public_id, "name": customer.name, "phone": customer.phone}


class AdminOrderSerializer(serializers.ModelSerializer):
    items = AdminOrderItemSerializer(many=True, read_only=True)
    delivery_area_label = serializers.CharField(
        source='get_delivery_area_display', read_only=True,
    )
    user_public_id = serializers.CharField(source="user.public_id", read_only=True, allow_null=True)
    shipping_zone_public_id = serializers.CharField(source="shipping_zone.public_id", read_only=True, allow_null=True)
    shipping_method_public_id = serializers.CharField(source="shipping_method.public_id", read_only=True, allow_null=True)
    customer = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'public_id', 'order_number', 'user_public_id', 'email', 'status',
            'subtotal', 'shipping_cost', 'total',
            'shipping_zone_public_id', 'shipping_method_public_id',
            'shipping_name', 'shipping_address', 'phone',
            'delivery_area', 'delivery_area_label', 'district',
            'tracking_number', 'customer',
            'courier_provider', 'courier_consignment_id', 'courier_tracking_code',
            'courier_status', 'sent_to_courier', 'customer_confirmation_sent_at',
            'extra_data', 'items', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'public_id', 'order_number', 'status', 'subtotal', 'shipping_cost', 'total',
            'courier_provider', 'courier_consignment_id', 'courier_tracking_code',
            'courier_status', 'sent_to_courier', 'customer_confirmation_sent_at',
            'created_at', 'updated_at',
        ]

    def get_customer(self, obj):
        customer = getattr(obj, "customer", None)
        if not customer:
            return None
        return {"public_id": customer.public_id, "name": customer.name, "phone": customer.phone}


class AdminOrderItemUpdateSerializer(serializers.Serializer):
    """
    Update an existing order item (dashboard order details edit).
    Identified by public_id; variant selected by variant_public_id.
    """

    public_id = serializers.CharField()
    variant_public_id = serializers.CharField(required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1)
    price = serializers.DecimalField(max_digits=10, decimal_places=2)


class AdminOrderUpdateSerializer(serializers.ModelSerializer):
    """
    Update an order and its items (variants/quantity/price) from the dashboard.
    """

    shipping_zone = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingZone.objects.all(),
        allow_null=True,
        required=False,
    )
    shipping_method = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingMethod.objects.all(),
        allow_null=True,
        required=False,
    )
    # Write-only: we accept item edits in PATCH/PUT, but we do not serialize them back
    # with this serializer (response uses AdminOrderSerializer).
    items = AdminOrderItemUpdateSerializer(many=True, required=False, write_only=True)

    class Meta:
        model = Order
        fields = [
            "public_id",
            "order_number",
            "email",
            "shipping_zone",
            "shipping_method",
            "shipping_name",
            "shipping_address",
            "phone",
            "district",
            "delivery_area",
            "tracking_number",
            "extra_data",
            "items",
            "total",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "order_number", "total", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_store = self.context.get("active_store")
        if not active_store:
            self.fields["shipping_zone"].queryset = ShippingZone.objects.none()
            self.fields["shipping_method"].queryset = ShippingMethod.objects.none()
            return
        self.fields["shipping_zone"].queryset = ShippingZone.objects.filter(store=active_store)
        self.fields["shipping_method"].queryset = ShippingMethod.objects.filter(store=active_store)

    def update(self, instance: Order, validated_data):
        try:
            with transaction.atomic():
                items = validated_data.pop("items", None)
                for k, v in validated_data.items():
                    setattr(instance, k, v)
                instance.save()

                if items is None:
                    return instance

                store = instance.store
                existing = {
                    oi.public_id: oi
                    for oi in OrderItem.objects.select_related("variant", "product").filter(order=instance)
                }

                # Update each provided item in-place and adjust stock by delta.
                subtotal = Decimal("0.00")
                for item in items:
                    item_public_id = item["public_id"]
                    oi = existing.get(item_public_id)
                    if not oi:
                        raise serializers.ValidationError({"items": [f"Order item {item_public_id} not found."]})

                    prev_product_id = str(oi.product_id)
                    prev_variant_id = oi.variant_id
                    prev_qty = int(oi.quantity)

                    variant_public_id = item.get("variant_public_id", None)
                    qty = int(item["quantity"])
                    price = item["price"]

                    variant_obj = None
                    if variant_public_id is not None:
                        try:
                            variant_obj = ProductVariant.objects.select_related("product").get(public_id=variant_public_id)
                        except ProductVariant.DoesNotExist:
                            raise serializers.ValidationError({"items": [f"Variant {variant_public_id} does not exist."]})
                        if str(variant_obj.product_id) != str(oi.product_id):
                            raise serializers.ValidationError({"items": ["Selected variant does not belong to the product."]})
                        if variant_obj.product.store_id != store.id:
                            raise serializers.ValidationError({"items": ["Selected variant does not belong to your active store."]})

                    # Adjust stock:
                    # - if variant target changes: restore old qty to old target, then reduce new qty from new target
                    # - else apply delta on same target
                    new_variant_id = variant_obj.pk if variant_obj else None
                    try:
                        if prev_variant_id != new_variant_id:
                            adjust_stock(product_id=prev_product_id, variant_id=prev_variant_id, delta_qty=-prev_qty)
                            adjust_stock(product_id=prev_product_id, variant_id=new_variant_id, delta_qty=qty)
                        else:
                            delta = qty - prev_qty
                            if delta != 0:
                                adjust_stock(product_id=prev_product_id, variant_id=prev_variant_id, delta_qty=delta)
                    except DjangoValidationError as e:
                        raise serializers.ValidationError(e.message_dict if hasattr(e, "message_dict") else {"detail": str(e)})

                    oi.variant = variant_obj
                    oi.quantity = qty
                    oi.price = price
                    oi.save(update_fields=["variant", "quantity", "price"])

                    subtotal += Decimal(str(price)) * Decimal(qty)

                quote = quote_shipping(
                    store=instance.store,
                    order_subtotal=subtotal,
                    delivery_area=(instance.delivery_area or "").strip().lower() or None,
                    district=(instance.district or "").strip() or None,
                    preferred_method_id=instance.shipping_method_id,
                    preferred_zone_id=instance.shipping_zone_id,
                )
                instance.subtotal = subtotal
                instance.shipping_cost = quote.shipping_cost
                instance.shipping_zone = quote.zone
                instance.shipping_method = quote.method
                instance.shipping_rate = quote.rate
                instance.total = subtotal + quote.shipping_cost
                instance.save(
                    update_fields=[
                        "subtotal",
                        "shipping_cost",
                        "shipping_zone",
                        "shipping_method",
                        "shipping_rate",
                        "total",
                    ]
                )
                return instance
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError(
                {"detail": "An unexpected error occurred. Please try again."}
            )


class AdminOrderItemWriteSerializer(serializers.Serializer):
    # Accept product_public_id (e.g. prd_xxx) — do NOT accept internal UUID/integer PKs
    product = StoreScopedProductSlugRelatedField(slug_field="public_id", queryset=Product.objects.all())
    variant_public_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    quantity = serializers.IntegerField(min_value=1)
    price = serializers.DecimalField(max_digits=10, decimal_places=2)

    def validate(self, attrs):
        if attrs.get("variant_public_id") == "":
            attrs["variant_public_id"] = None
        return attrs


class AdminOrderCreateSerializer(serializers.ModelSerializer):
    """
    Create orders from the dashboard with inline items (similar to Django admin UI).
    """

    shipping_zone = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingZone.objects.all(),
        allow_null=True,
        required=False,
    )
    shipping_method = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingMethod.objects.all(),
        allow_null=True,
        required=False,
    )
    items = AdminOrderItemWriteSerializer(many=True, write_only=True)
    phone = serializers.CharField(max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = Order
        fields = [
            "public_id",
            "order_number",
            "email",
            "shipping_zone",
            "shipping_method",
            "shipping_name",
            "shipping_address",
            "phone",
            "district",
            "delivery_area",
            "tracking_number",
            "extra_data",
            "items",
            "total",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "order_number", "total", "created_at", "updated_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        active_store = self.context.get("active_store")
        if not active_store:
            self.fields["shipping_zone"].queryset = ShippingZone.objects.none()
            self.fields["shipping_method"].queryset = ShippingMethod.objects.none()
            return
        self.fields["shipping_zone"].queryset = ShippingZone.objects.filter(store=active_store)
        self.fields["shipping_method"].queryset = ShippingMethod.objects.filter(store=active_store)

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        return items

    def validate_phone(self, value):
        raw = (value or "").strip()
        if not raw:
            raise serializers.ValidationError("Required.")
        digits = "".join(c for c in raw if c.isdigit())
        if len(digits) != 11 or not digits.startswith("01"):
            raise serializers.ValidationError(
                "Phone must be 11 digits, start with 01, and contain only numbers."
            )
        return digits

    def create(self, validated_data):
        with transaction.atomic():
            items = validated_data.pop("items", [])
            store = validated_data.pop("store")

            order = Order.objects.create(store=store, **validated_data)

            subtotal = Decimal("0.00")
            for item in items:
                product_obj = item["product"]  # Product instance resolved via SlugRelatedField
                if product_obj.store_id != store.id:
                    raise serializers.ValidationError(
                        {"items": ["Selected product does not belong to your active store."]}
                    )
                variant_public_id = item.get("variant_public_id")
                quantity = item["quantity"]
                price = item["price"]

                variant_obj = None
                if variant_public_id is not None:
                    try:
                        variant_obj = ProductVariant.objects.select_related("product").get(
                            public_id=variant_public_id
                        )
                    except ProductVariant.DoesNotExist:
                        raise serializers.ValidationError(
                            {"items": [f"Variant {variant_public_id} does not exist."]}
                        )
                    if variant_obj.product_id != product_obj.pk:
                        raise serializers.ValidationError(
                            {"items": ["Selected variant does not belong to the product."]}
                        )
                    if variant_obj.product.store_id != store.id:
                        raise serializers.ValidationError(
                            {"items": ["Selected variant does not belong to your active store."]}
                        )

                order_item = OrderItem.objects.create(
                    order=order,
                    product=product_obj,
                    variant=variant_obj,
                    quantity=quantity,
                    price=price,
                )
                # Reduce stock for created order items (dashboard-created orders).
                try:
                    adjust_stock(
                        product_id=product_obj.pk,
                        variant_id=variant_obj.pk if variant_obj else None,
                        delta_qty=quantity,
                    )
                except DjangoValidationError as e:
                    raise serializers.ValidationError(e.message_dict if hasattr(e, "message_dict") else {"detail": str(e)})
                subtotal += Decimal(str(order_item.price)) * Decimal(order_item.quantity)

            quote = quote_shipping(
                store=order.store,
                order_subtotal=subtotal,
                delivery_area=(order.delivery_area or "").strip().lower() or None,
                district=(order.district or "").strip() or None,
                preferred_method_id=order.shipping_method_id,
                preferred_zone_id=order.shipping_zone_id,
            )
            order.subtotal = subtotal
            order.shipping_cost = quote.shipping_cost
            order.shipping_zone = quote.zone
            order.shipping_method = quote.method
            order.shipping_rate = quote.rate
            order.total = subtotal + quote.shipping_cost
            order.save(
                update_fields=[
                    "subtotal",
                    "shipping_cost",
                    "shipping_zone",
                    "shipping_method",
                    "shipping_rate",
                    "total",
                ]
            )
            resolve_and_attach_customer(
                order,
                store=store,
                name=order.shipping_name,
                phone=order.phone,
                email=order.email,
                address=order.shipping_address,
            )
            return order
