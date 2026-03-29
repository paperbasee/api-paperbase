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


class StorefrontProductVariantSerializer(SafeModelSerializer):
    """Storefront PDP: sellable SKU with options (no internal stock_source / is_active)."""
    price = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()
    stock_status = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = [
            "public_id",
            "sku",
            "available_quantity",
            "stock_status",
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

    def get_stock_status(self, obj):
        return stock_status_for_quantity(
            int(self._quantity(obj)),
            self._low_threshold(),
        )


class StorefrontProductListSerializer(SafeModelSerializer):
    """Storefront product card: pricing, stock, brand/SKU for list UX."""

    image_url = serializers.SerializerMethodField()
    original_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )
    stock_status = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()
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
            "price",
            "original_price",
            "image_url",
            "category_public_id",
            "category_slug",
            "category_name",
            "slug",
            "stock_status",
            "available_quantity",
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

    def _total_stock_for_status(self, obj) -> int:
        n = self._active_variant_count(obj)
        if n > 0:
            s = getattr(obj, "_pub_variant_stock_sum", None)
            if s is None:
                from django.db.models import Sum as SumAgg

                s = obj.variants.filter(is_active=True).aggregate(x=SumAgg("inventory__quantity"))["x"]
            return int(s or 0)
        base = getattr(obj, "_pub_base_inventory_qty", None)
        return int(base or 0)

    def get_available_quantity(self, obj):
        return int(self._total_stock_for_status(obj))

    def get_stock_status(self, obj):
        return stock_status_for_quantity(
            int(self._total_stock_for_status(obj)),
            self._low_threshold(),
        )


class StorefrontProductDetailSerializer(SafeModelSerializer):
    """Storefront PDP: gallery, variants, product-level stock and pricing."""

    image_url = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    original_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )
    stock_status = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()
    variants = StorefrontProductVariantSerializer(many=True, read_only=True)
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
            "stock_status",
            "available_quantity",
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

    def _total_stock_for_status(self, obj) -> int:
        n = self._active_variant_count(obj)
        if n > 0:
            s = getattr(obj, "_pub_variant_stock_sum", None)
            if s is None:
                from django.db.models import Sum as SumAgg

                s = obj.variants.filter(is_active=True).aggregate(x=SumAgg("inventory__quantity"))["x"]
            return int(s or 0)
        base = getattr(obj, "_pub_base_inventory_qty", None)
        return int(base or 0)

    def get_available_quantity(self, obj):
        return int(self._total_stock_for_status(obj))

    def get_stock_status(self, obj):
        return stock_status_for_quantity(
            int(self._total_stock_for_status(obj)),
            self._low_threshold(),
        )


class StorefrontCategorySerializer(SafeModelSerializer):
    """Storefront category tree node (active-only querysets; no is_active flag)."""

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
        ]

    def get_image_url(self, obj):
        return absolute_media_url(obj.image, self.context.get("request"))
