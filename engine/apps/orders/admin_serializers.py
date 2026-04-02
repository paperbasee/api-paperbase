from decimal import Decimal

from rest_framework import serializers

from django.core.exceptions import ValidationError as DjangoValidationError

from engine.core.serializers import SafeModelSerializer
from django.db import transaction

from engine.apps.products.models import Product, ProductVariant
from engine.apps.orders.stock import adjust_stock
from engine.apps.shipping.models import ShippingMethod, ShippingZone
from engine.apps.products.variant_utils import unit_price_for_line
from engine.apps.orders.order_financials import money, reference_unit_price
from engine.apps.orders.services import (
    recalculate_order_totals,
    resolve_active_store_product,
    resolve_active_variant_for_product,
    resolve_and_attach_customer,
    restore_order_item_stock,
    write_order_item_financials,
)

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
        return Product.objects.filter(
            store=active_store,
            is_active=True,
            status=Product.Status.ACTIVE,
        )


class AdminOrderItemSerializer(SafeModelSerializer):
    # Expose public_id only — do NOT expose product UUID/integer PK
    product = serializers.SerializerMethodField()
    product_public_id = serializers.SerializerMethodField()
    product_name = serializers.SerializerMethodField()
    product_brand = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    is_unavailable = serializers.SerializerMethodField()
    product_image = serializers.SerializerMethodField()
    variant_public_id = serializers.CharField(source="variant.public_id", read_only=True, allow_null=True)
    variant_sku = serializers.CharField(source="variant.sku", read_only=True, allow_null=True)
    variant_inventory_quantity = serializers.SerializerMethodField()
    variant_option_labels = serializers.SerializerMethodField()
    catalog_unit_price = serializers.SerializerMethodField()
    catalog_list_price = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            'public_id', 'product', 'product_public_id', 'product_name', 'product_brand', 'product_image',
            'status',
            'is_unavailable',
            'variant_public_id', 'variant_sku', 'variant_inventory_quantity', 'variant_option_labels',
            'quantity', 'unit_price', 'original_price', 'discount_amount', 'line_subtotal', 'line_total',
            'catalog_unit_price', 'catalog_list_price',
        ]
        read_only_fields = ['public_id']

    def get_product(self, obj):
        if not obj.product:
            return None
        return {
            "public_id": obj.product.public_id,
            "name": obj.product.name,
        }

    def get_product_public_id(self, obj):
        return obj.product.public_id if obj.product else None

    def get_product_name(self, obj):
        return obj.product.name if obj.product else "Unavailable"

    def get_product_brand(self, obj):
        return obj.product.brand if obj.product else ""

    def get_status(self, obj):
        return "active" if obj.product else "deleted"

    def get_is_unavailable(self, obj):
        return obj.product is None

    def get_product_image(self, obj):
        if obj.product and obj.product.image and hasattr(obj.product.image, 'url'):
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

    def get_variant_inventory_quantity(self, obj):
        v = getattr(obj, "variant", None)
        if not v:
            return None
        inv = getattr(v, "inventory", None)
        if inv is None:
            return 0
        return int(inv.quantity or 0)

    def get_catalog_unit_price(self, obj):
        if not obj.product:
            return None
        return str(money(unit_price_for_line(obj.product, getattr(obj, "variant", None))))

    def get_catalog_list_price(self, obj):
        if not obj.product:
            return None
        return str(money(reference_unit_price(obj.product, getattr(obj, "variant", None))))


class AdminOrderListSerializer(SafeModelSerializer):
    # List/detail callers must annotate queryset with items_count=Count("items")
    # (see AdminOrderViewSet.get_queryset and DashboardStatsView); avoids N+1.
    items_count = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()
    has_unavailable_products = serializers.SerializerMethodField()
    unavailable_products_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'public_id', 'order_number', 'email', 'status',
            'subtotal_before_discount', 'discount_total', 'subtotal_after_discount',
            'shipping_cost', 'total',
            'shipping_name', 'phone', 'district',
            'items_count', 'customer',
            'has_unavailable_products', 'unavailable_products_count',
            'courier_provider', 'courier_consignment_id',
            'sent_to_courier', 'customer_confirmation_sent_at',
            'created_at', 'updated_at',
        ]

    def get_items_count(self, obj):
        return obj.items_count

    def get_customer(self, obj):
        customer = getattr(obj, "customer", None)
        if not customer:
            return None
        return {"public_id": customer.public_id, "name": customer.name, "phone": customer.phone}

    def get_unavailable_products_count(self, obj):
        return obj.items.filter(product__isnull=True).count()

    def get_has_unavailable_products(self, obj):
        return self.get_unavailable_products_count(obj) > 0


class AdminOrderSerializer(SafeModelSerializer):
    items = AdminOrderItemSerializer(many=True, read_only=True)
    user_public_id = serializers.CharField(source="user.public_id", read_only=True, allow_null=True)
    shipping_zone_public_id = serializers.CharField(source="shipping_zone.public_id", read_only=True, allow_null=True)
    shipping_method_public_id = serializers.CharField(source="shipping_method.public_id", read_only=True, allow_null=True)
    shipping_rate_public_id = serializers.CharField(
        source="shipping_rate.public_id", read_only=True, allow_null=True
    )
    customer = serializers.SerializerMethodField()
    has_unavailable_products = serializers.SerializerMethodField()
    unavailable_products_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'public_id', 'order_number', 'user_public_id', 'email', 'status',
            'subtotal_before_discount', 'discount_total', 'subtotal_after_discount',
            'shipping_cost', 'total',
            'shipping_zone_public_id', 'shipping_method_public_id', 'shipping_rate_public_id',
            'shipping_name', 'shipping_address', 'phone',
            'district',
            'customer',
            'courier_provider', 'courier_consignment_id',
            'sent_to_courier', 'customer_confirmation_sent_at',
            'pricing_snapshot', 'items', 'created_at', 'updated_at',
            'has_unavailable_products', 'unavailable_products_count',
        ]
        read_only_fields = [
            'public_id', 'order_number', 'status',
            'subtotal_before_discount', 'discount_total', 'subtotal_after_discount',
            'shipping_cost', 'total',
            'courier_provider', 'courier_consignment_id',
            'sent_to_courier', 'customer_confirmation_sent_at',
            'pricing_snapshot', 'created_at', 'updated_at',
        ]

    def get_customer(self, obj):
        customer = getattr(obj, "customer", None)
        if not customer:
            return None
        return {"public_id": customer.public_id, "name": customer.name, "phone": customer.phone}

    def get_unavailable_products_count(self, obj):
        return obj.items.filter(product__isnull=True).count()

    def get_has_unavailable_products(self, obj):
        return self.get_unavailable_products_count(obj) > 0


class AdminOrderItemUpdateSerializer(serializers.Serializer):
    """
    Update an existing order item (dashboard order details edit).
    Identified by public_id; variant selected by variant_public_id.
    """

    public_id = serializers.CharField(required=False)
    product_public_id = serializers.CharField(required=False)
    remove = serializers.BooleanField(required=False, default=False)
    variant_public_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    quantity = serializers.IntegerField(min_value=1, required=False)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)

    def validate(self, attrs):
        if attrs.get("variant_public_id") == "":
            attrs["variant_public_id"] = None
        is_remove = bool(attrs.get("remove"))
        has_public_id = bool(attrs.get("public_id"))
        has_product = bool(attrs.get("product_public_id"))

        if is_remove:
            if not has_public_id:
                raise serializers.ValidationError("public_id is required when remove=true.")
            return attrs

        if has_public_id:
            if "quantity" not in attrs:
                raise serializers.ValidationError("quantity is required for existing items.")
            return attrs

        if has_product:
            if "quantity" not in attrs:
                raise serializers.ValidationError("quantity is required for new items.")
            return attrs

        raise serializers.ValidationError("Either public_id or product_public_id is required.")


class AdminOrderUpdateSerializer(SafeModelSerializer):
    """
    Update an order and its items (variants/quantity/price) from the dashboard.
    """

    shipping_zone_public_id = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingZone.objects.all(),
        allow_null=False,
        required=False,
        source='shipping_zone',
    )
    shipping_method_public_id = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingMethod.objects.all(),
        allow_null=True,
        required=False,
        source='shipping_method',
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
            "shipping_zone_public_id",
            "shipping_method_public_id",
            "shipping_name",
            "shipping_address",
            "phone",
            "district",
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
            self.fields["shipping_zone_public_id"].queryset = ShippingZone.objects.none()
            self.fields["shipping_method_public_id"].queryset = ShippingMethod.objects.none()
            return
        self.fields["shipping_zone_public_id"].queryset = ShippingZone.objects.filter(store=active_store)
        self.fields["shipping_method_public_id"].queryset = ShippingMethod.objects.filter(
            store=active_store
        )

    def update(self, instance: Order, validated_data):
        try:
            with transaction.atomic():
                items = validated_data.pop("items", None)
                for k, v in validated_data.items():
                    setattr(instance, k, v)
                instance.save()

                if items is None:
                    recalculate_order_totals(instance)
                    return instance

                store = instance.store
                existing = {
                    oi.public_id: oi
                    for oi in OrderItem.objects.select_related("variant", "product").filter(order=instance)
                }
                for item in items:
                    item_public_id = item.get("public_id")
                    product_public_id = item.get("product_public_id")
                    is_remove = bool(item.get("remove"))

                    if is_remove:
                        oi = existing.get(item_public_id)
                        if not oi:
                            raise serializers.ValidationError({"items": [f"Order item {item_public_id} not found."]})
                        if oi.product_id:
                            try:
                                restore_order_item_stock(
                                    store_id=store.id,
                                    product_id=oi.product_id,
                                    variant_id=oi.variant_id,
                                    quantity=oi.quantity,
                                )
                            except DjangoValidationError as e:
                                raise serializers.ValidationError(
                                    e.message_dict if hasattr(e, "message_dict") else {"detail": str(e)}
                                )
                        oi.delete()
                        continue

                    qty = int(item["quantity"])
                    variant_public_id = item.get("variant_public_id")

                    if item_public_id:
                        oi = existing.get(item_public_id)
                        if not oi:
                            raise serializers.ValidationError({"items": [f"Order item {item_public_id} not found."]})
                        if not oi.product_id:
                            raise serializers.ValidationError({"items": ["Selected product is unavailable."]})

                        prev_product_id = str(oi.product_id)
                        prev_variant_id = oi.variant_id
                        prev_qty = int(oi.quantity)
                        variant_obj = resolve_active_variant_for_product(
                            store=store,
                            product=oi.product,
                            variant_public_id=variant_public_id,
                        )

                        new_variant_id = variant_obj.pk if variant_obj else None
                        try:
                            if prev_variant_id != new_variant_id:
                                adjust_stock(
                                    product_id=prev_product_id,
                                    variant_id=prev_variant_id,
                                    delta_qty=-prev_qty,
                                    store_id=store.id,
                                )
                                adjust_stock(
                                    product_id=prev_product_id,
                                    variant_id=new_variant_id,
                                    delta_qty=qty,
                                    store_id=store.id,
                                )
                            else:
                                delta = qty - prev_qty
                                if delta != 0:
                                    adjust_stock(
                                        product_id=prev_product_id,
                                        variant_id=prev_variant_id,
                                        delta_qty=delta,
                                        store_id=store.id,
                                    )
                        except DjangoValidationError as e:
                            raise serializers.ValidationError(
                                e.message_dict if hasattr(e, "message_dict") else {"detail": str(e)}
                            )

                        oi.variant = variant_obj
                        oi.quantity = qty
                        catalog_unit = unit_price_for_line(oi.product, variant_obj)
                        if item.get("unit_price") is not None:
                            chosen = money(item["unit_price"])
                        else:
                            chosen = catalog_unit
                        write_order_item_financials(
                            oi,
                            product=oi.product,
                            variant=variant_obj,
                            quantity=qty,
                            unit_price=chosen,
                        )
                        oi.save(
                            update_fields=[
                                "variant",
                                "quantity",
                                "unit_price",
                                "original_price",
                                "discount_amount",
                                "line_subtotal",
                                "line_total",
                            ]
                        )
                        continue

                    if not product_public_id:
                        raise serializers.ValidationError(
                            {"items": ["product_public_id is required for new item."]}
                        )
                    try:
                        product_obj = resolve_active_store_product(
                            store=store,
                            product_public_id=product_public_id,
                        )
                        variant_obj = resolve_active_variant_for_product(
                            store=store,
                            product=product_obj,
                            variant_public_id=variant_public_id,
                        )
                    except ValueError as e:
                        raise serializers.ValidationError({"items": [str(e)]})

                    try:
                        adjust_stock(
                            product_id=product_obj.pk,
                            variant_id=variant_obj.pk if variant_obj else None,
                            delta_qty=qty,
                            store_id=store.id,
                        )
                    except DjangoValidationError as e:
                        raise serializers.ValidationError(
                            e.message_dict if hasattr(e, "message_dict") else {"detail": str(e)}
                        )
                    catalog_unit = unit_price_for_line(product_obj, variant_obj)
                    if item.get("unit_price") is not None:
                        chosen = money(item["unit_price"])
                    else:
                        chosen = catalog_unit
                    oi_new = OrderItem(
                        order=instance,
                        product=product_obj,
                        variant=variant_obj,
                        quantity=qty,
                        unit_price=Decimal("0.00"),
                        original_price=Decimal("0.00"),
                        discount_amount=Decimal("0.00"),
                        line_subtotal=Decimal("0.00"),
                        line_total=Decimal("0.00"),
                    )
                    write_order_item_financials(
                        oi_new,
                        product=product_obj,
                        variant=variant_obj,
                        quantity=qty,
                        unit_price=chosen,
                    )
                    oi_new.save()

                recalculate_order_totals(instance)
                instance.refresh_from_db()
                return instance
        except serializers.ValidationError:
            raise
        except Exception:
            raise serializers.ValidationError(
                {"detail": "An unexpected error occurred. Please try again."}
            )


class AdminOrderItemWriteSerializer(serializers.Serializer):
    product_public_id = StoreScopedProductSlugRelatedField(
        slug_field="public_id",
        queryset=Product.objects.all(),
        source="product",
    )
    variant_public_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    quantity = serializers.IntegerField(min_value=1)
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)

    def validate(self, attrs):
        if attrs.get("variant_public_id") == "":
            attrs["variant_public_id"] = None
        return attrs


class AdminOrderCreateSerializer(SafeModelSerializer):
    """
    Create orders from the dashboard with inline items (similar to Django admin UI).
    """

    shipping_zone_public_id = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingZone.objects.all(),
        allow_null=False,
        required=True,
        source='shipping_zone',
    )
    shipping_method_public_id = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=ShippingMethod.objects.all(),
        allow_null=True,
        required=False,
        source='shipping_method',
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
            "shipping_zone_public_id",
            "shipping_method_public_id",
            "shipping_name",
            "shipping_address",
            "phone",
            "district",
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
            self.fields["shipping_zone_public_id"].queryset = ShippingZone.objects.none()
            self.fields["shipping_method_public_id"].queryset = ShippingMethod.objects.none()
            return
        self.fields["shipping_zone_public_id"].queryset = ShippingZone.objects.filter(store=active_store)
        self.fields["shipping_method_public_id"].queryset = ShippingMethod.objects.filter(
            store=active_store
        )

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

            for item in items:
                product_obj = item["product"]  # Product instance resolved via SlugRelatedField
                if product_obj.store_id != store.id:
                    raise serializers.ValidationError(
                        {"items": ["Selected product does not belong to your active store."]}
                    )
                if not product_obj.is_active or product_obj.status != Product.Status.ACTIVE:
                    raise serializers.ValidationError(
                        {"items": ["Selected product is unavailable."]}
                    )
                variant_public_id = item.get("variant_public_id")
                quantity = item["quantity"]

                variant_obj = None
                if variant_public_id is not None:
                    try:
                        variant_obj = ProductVariant.objects.select_related("product").get(
                            public_id=variant_public_id,
                            product_id=product_obj.pk,
                            product__store=store,
                            product__is_active=True,
                            product__status=Product.Status.ACTIVE,
                            is_active=True,
                        )
                    except ProductVariant.DoesNotExist:
                        raise serializers.ValidationError(
                            {"items": [f"Variant {variant_public_id} is unavailable."]}
                        )

                catalog_unit = unit_price_for_line(product_obj, variant_obj)
                if item.get("unit_price") is not None:
                    chosen = money(item["unit_price"])
                else:
                    chosen = catalog_unit
                order_item = OrderItem(
                    order=order,
                    product=product_obj,
                    variant=variant_obj,
                    quantity=quantity,
                    unit_price=Decimal("0.00"),
                    original_price=Decimal("0.00"),
                    discount_amount=Decimal("0.00"),
                    line_subtotal=Decimal("0.00"),
                    line_total=Decimal("0.00"),
                )
                write_order_item_financials(
                    order_item,
                    product=product_obj,
                    variant=variant_obj,
                    quantity=quantity,
                    unit_price=chosen,
                )
                order_item.save()
                # Reduce stock for created order items (dashboard-created orders).
                try:
                    adjust_stock(
                        product_id=product_obj.pk,
                        variant_id=variant_obj.pk if variant_obj else None,
                        delta_qty=quantity,
                        store_id=store.id,
                    )
                except DjangoValidationError as e:
                    raise serializers.ValidationError(e.message_dict if hasattr(e, "message_dict") else {"detail": str(e)})

            recalculate_order_totals(order)
            resolve_and_attach_customer(
                order,
                store=store,
                name=order.shipping_name,
                phone=order.phone,
                email=order.email,
                address=order.shipping_address,
            )
            return order
