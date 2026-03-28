from rest_framework import serializers

from engine.core.media_urls import absolute_media_url
from engine.core.serializers import SafeModelSerializer

from .models import (
    Category,
    Product,
    ProductImage,
    ProductVariant,
)
from .stock_signals import stock_status_for_quantity


class ProductImageSerializer(SafeModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ["public_id", "image_url", "alt", "order"]

    def get_image_url(self, obj):
        return absolute_media_url(obj.image, self.context.get("request"))


class ProductVariantPublicSerializer(SafeModelSerializer):
    """Storefront: one sellable SKU with options (color/size/etc.)."""
    price = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()
    stock_source = serializers.SerializerMethodField()
    stock_status = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = [
            "public_id",
            "sku",
            "available_quantity",
            "stock_source",
            "stock_status",
            "is_active",
            "price",
            "options",
        ]

    def _low_threshold(self) -> int:
        return int(self.context.get("low_stock_threshold", 5))

    def get_price(self, obj):
        return str(obj.effective_price)

    def get_options(self, obj):
        rows = []
        for link in obj.attribute_values.select_related("attribute_value__attribute").all():
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

    def _quantity(self, obj):
        inv = getattr(obj, "inventory", None)
        if inv is None:
            return 0
        return int(inv.quantity or 0)

    def get_available_quantity(self, obj):
        return self._quantity(obj)

    def get_stock_source(self, obj):
        return "variant_inventory"

    def get_stock_status(self, obj):
        return stock_status_for_quantity(
            int(self._quantity(obj)),
            self._low_threshold(),
        )


class ProductListSerializer(SafeModelSerializer):
    """Storefront product card: snake_case, inventory-aligned totals."""

    image_url = serializers.SerializerMethodField()
    original_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )
    total_stock = serializers.SerializerMethodField()
    stock_source = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()
    stock_status = serializers.SerializerMethodField()
    variant_count = serializers.SerializerMethodField()
    category_public_id = serializers.CharField(source="category.public_id", read_only=True)
    category_slug = serializers.CharField(source="category.slug", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Product
        fields = [
            "public_id",
            "name",
            "brand",
            "sku",
            "stock_tracking",
            "price",
            "original_price",
            "image_url",
            "category_public_id",
            "category_slug",
            "category_name",
            "slug",
            "total_stock",
            "stock_source",
            "available_quantity",
            "stock_status",
            "variant_count",
            "extra_data",
        ]

    def _low_threshold(self) -> int:
        return int(self.context.get("low_stock_threshold", 5))

    def _active_variant_count(self, obj) -> int:
        n = getattr(obj, "_pub_variant_count", None)
        if n is not None:
            return int(n)
        return obj.variants.filter(is_active=True).count()

    def get_image_url(self, obj):
        return absolute_media_url(obj.image, self.context.get("request"))

    def get_variant_count(self, obj):
        return self._active_variant_count(obj)

    def get_total_stock(self, obj):
        n = self._active_variant_count(obj)
        if n > 0:
            s = getattr(obj, "_pub_variant_stock_sum", None)
            if s is None:
                from django.db.models import Sum as SumAgg

                s = obj.variants.filter(is_active=True).aggregate(x=SumAgg("inventory__quantity"))["x"]
            return int(s or 0)
        base = getattr(obj, "_pub_base_inventory_qty", None)
        if base is not None:
            return int(base)
        return int(obj.stock or 0)

    def get_stock_source(self, obj):
        n = self._active_variant_count(obj)
        if n > 0:
            return "variant_inventory_sum"
        base = getattr(obj, "_pub_base_inventory_qty", None)
        if base is not None:
            return "product_inventory"
        return "product_stock_cache"

    def get_available_quantity(self, obj):
        return self.get_total_stock(obj)

    def get_stock_status(self, obj):
        return stock_status_for_quantity(
            int(self.get_total_stock(obj)),
            self._low_threshold(),
        )


class ProductDetailSerializer(SafeModelSerializer):
    """Storefront detail: gallery with alt text, variants, aggregated stock."""

    image_url = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    original_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )
    total_stock = serializers.SerializerMethodField()
    stock_source = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()
    stock_status = serializers.SerializerMethodField()
    variant_count = serializers.SerializerMethodField()
    variants = ProductVariantPublicSerializer(many=True, read_only=True)
    category_public_id = serializers.CharField(source="category.public_id", read_only=True)
    category_slug = serializers.CharField(source="category.slug", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = Product
        fields = [
            "public_id",
            "name",
            "brand",
            "sku",
            "stock_tracking",
            "slug",
            "price",
            "original_price",
            "image_url",
            "images",
            "category_public_id",
            "category_slug",
            "category_name",
            "description",
            "created_at",
            "total_stock",
            "stock_source",
            "available_quantity",
            "stock_status",
            "variant_count",
            "variants",
            "extra_data",
        ]

    def _low_threshold(self) -> int:
        return int(self.context.get("low_stock_threshold", 5))

    def _active_variant_count(self, obj) -> int:
        n = getattr(obj, "_pub_variant_count", None)
        if n is not None:
            return int(n)
        return obj.variants.filter(is_active=True).count()

    def get_image_url(self, obj):
        return absolute_media_url(obj.image, self.context.get("request"))

    def get_images(self, obj):
        qs = obj.images.all().order_by("order", "id")
        if not qs.exists():
            return []
        return ProductImageSerializer(
            qs,
            many=True,
            context={"request": self.context.get("request")},
        ).data

    def get_variant_count(self, obj):
        return self._active_variant_count(obj)

    def get_total_stock(self, obj):
        n = self._active_variant_count(obj)
        if n > 0:
            s = getattr(obj, "_pub_variant_stock_sum", None)
            if s is None:
                from django.db.models import Sum as SumAgg

                s = obj.variants.filter(is_active=True).aggregate(x=SumAgg("inventory__quantity"))["x"]
            return int(s or 0)
        base = getattr(obj, "_pub_base_inventory_qty", None)
        if base is not None:
            return int(base)
        return int(obj.stock or 0)

    def get_stock_source(self, obj):
        n = self._active_variant_count(obj)
        if n > 0:
            return "variant_inventory_sum"
        base = getattr(obj, "_pub_base_inventory_qty", None)
        if base is not None:
            return "product_inventory"
        return "product_stock_cache"

    def get_available_quantity(self, obj):
        return self.get_total_stock(obj)

    def get_stock_status(self, obj):
        return stock_status_for_quantity(
            int(self.get_total_stock(obj)),
            self._low_threshold(),
        )


class CategorySerializer(SafeModelSerializer):
    """Serializer for category tree nodes."""

    image_url = serializers.SerializerMethodField()
    parent_public_id = serializers.CharField(source="parent.public_id", read_only=True, allow_null=True)

    class Meta:
        model = Category
        fields = [
            "public_id",
            "name",
            "slug",
            "description",
            "image_url",
            "parent_public_id",
            "order",
            "is_active",
        ]

    def get_image_url(self, obj):
        return absolute_media_url(obj.image, self.context.get("request"))
