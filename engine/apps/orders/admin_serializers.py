from decimal import Decimal

from rest_framework import serializers

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from engine.apps.products.models import ProductVariant
from engine.apps.orders.stock import adjust_stock
from engine.apps.shipping.service import quote_shipping

from .models import Order, OrderItem

def _shipping_cost_for_order(order: Order, *, order_subtotal: Decimal) -> Decimal:
    quote = quote_shipping(
        store=order.store,
        order_subtotal=order_subtotal,
        delivery_area=(order.delivery_area or "").strip().lower() or None,
        district=(order.district or "").strip() or None,
    )
    return quote.shipping_cost


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
    variant = serializers.IntegerField(source="variant_id", read_only=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True, allow_null=True)
    variant_stock_quantity = serializers.IntegerField(source="variant.stock_quantity", read_only=True, allow_null=True)
    variant_option_labels = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            'id', 'public_id', 'product', 'product_name', 'product_brand', 'product_image',
            'variant', 'variant_sku', 'variant_stock_quantity', 'variant_option_labels',
            'quantity', 'price', 'original_price',
        ]
        read_only_fields = ['id', 'public_id']

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
    delivery_area_label = serializers.CharField(
        source='get_delivery_area_display', read_only=True,
    )

    class Meta:
        model = Order
        fields = [
            'id', 'public_id', 'order_number', 'email', 'status', 'subtotal', 'shipping_cost', 'total',
            'shipping_name', 'phone', 'district', 'delivery_area',
            'delivery_area_label', 'items_count', 'extra_data', 'created_at', 'updated_at',
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
            'id', 'public_id', 'order_number', 'user', 'email', 'status', 'subtotal', 'shipping_cost', 'total',
            'shipping_zone', 'shipping_method',
            'shipping_name', 'shipping_address', 'phone',
            'delivery_area', 'delivery_area_label', 'district',
            'tracking_number', 'extra_data', 'items', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'public_id', 'order_number', 'subtotal', 'shipping_cost', 'total', 'created_at', 'updated_at',
        ]


class AdminOrderItemUpdateSerializer(serializers.Serializer):
    """
    Update an existing order item (dashboard order details edit).
    """

    id = serializers.IntegerField()
    variant = serializers.IntegerField(required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1)
    price = serializers.DecimalField(max_digits=10, decimal_places=2)


class AdminOrderUpdateSerializer(serializers.ModelSerializer):
    """
    Update an order and its items (variants/quantity/price) from the dashboard.
    """

    # Write-only: we accept item edits in PATCH/PUT, but we do not serialize them back
    # with this serializer (response uses AdminOrderSerializer).
    items = AdminOrderItemUpdateSerializer(many=True, required=False, write_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "public_id",
            "order_number",
            "email",
            "status",
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
        read_only_fields = ["id", "public_id", "order_number", "total", "created_at", "updated_at"]

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
                    oi.id: oi
                    for oi in OrderItem.objects.select_related("variant", "product").filter(order=instance)
                }

                # Update each provided item in-place and adjust stock by delta.
                subtotal = Decimal("0.00")
                for item in items:
                    item_id = item["id"]
                    oi = existing.get(item_id)
                    if not oi:
                        raise serializers.ValidationError({"items": [f"Order item {item_id} not found."]})

                    prev_product_id = str(oi.product_id)
                    prev_variant_id = oi.variant_id
                    prev_qty = int(oi.quantity)

                    variant_id = item.get("variant", None)
                    qty = int(item["quantity"])
                    price = item["price"]

                    variant_obj = None
                    if variant_id is not None:
                        try:
                            variant_obj = ProductVariant.objects.select_related("product").get(pk=variant_id)
                        except ProductVariant.DoesNotExist:
                            raise serializers.ValidationError({"items": [f"Variant {variant_id} does not exist."]})
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
        except Exception as e:
            # Convert unexpected exceptions into a safe error response (prevents opaque 500s).
            raise serializers.ValidationError({"detail": str(e)})


class AdminOrderItemWriteSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    variant = serializers.IntegerField(required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1)
    price = serializers.DecimalField(max_digits=10, decimal_places=2)


class AdminOrderCreateSerializer(serializers.ModelSerializer):
    """
    Create orders from the dashboard with inline items (similar to Django admin UI).
    """

    items = AdminOrderItemWriteSerializer(many=True, write_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "public_id",
            "order_number",
            "email",
            "status",
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
        read_only_fields = ["id", "public_id", "order_number", "total", "created_at", "updated_at"]

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        return items

    def create(self, validated_data):
        items = validated_data.pop("items", [])
        store = validated_data.pop("store")

        order = Order.objects.create(store=store, **validated_data)

        subtotal = Decimal("0.00")
        for item in items:
            product_id = item["product"]
            variant_id = item.get("variant")
            quantity = item["quantity"]
            price = item["price"]

            variant_obj = None
            if variant_id is not None:
                try:
                    variant_obj = ProductVariant.objects.select_related("product").get(
                        pk=variant_id
                    )
                except ProductVariant.DoesNotExist:
                    raise serializers.ValidationError(
                        {"items": [f"Variant {variant_id} does not exist."]}
                    )
                if str(variant_obj.product_id) != str(product_id):
                    raise serializers.ValidationError(
                        {"items": ["Selected variant does not belong to the product."]}
                    )
                if variant_obj.product.store_id != store.id:
                    raise serializers.ValidationError(
                        {"items": ["Selected variant does not belong to your active store."]}
                    )

            order_item = OrderItem.objects.create(
                order=order,
                product_id=product_id,
                variant=variant_obj,
                quantity=quantity,
                price=price,
            )
            # Reduce stock for created order items (dashboard-created orders).
            try:
                adjust_stock(
                    product_id=product_id,
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
        return order


class AdminOrderStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Order.Status.choices)
