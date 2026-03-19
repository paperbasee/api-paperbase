from rest_framework import serializers

from .constants import MAX_PRODUCT_IMAGES_TOTAL
from .models import (
    Category,
    Product,
    ProductAttribute,
    ProductAttributeValue,
    ProductImage,
    ProductVariant,
    ProductVariantAttribute,
)

def _compact_code(raw: str, *, max_len: int) -> str:
    from django.utils.text import slugify

    s = (raw or "").strip()
    if not s:
        return ""
    s = slugify(s).replace("-", "")
    s = "".join(ch for ch in s if ch.isalnum())
    return s.upper()[:max_len]


def _variant_option_codes(value_ids: list[int]) -> list[str]:
    if not value_ids:
        return []
    values = (
        ProductAttributeValue.objects.filter(pk__in=value_ids)
        .select_related("attribute")
        .order_by("attribute__order", "order", "pk")
    )
    out: list[str] = []
    for v in values:
        seg = _compact_code(v.value, max_len=4) or _compact_code(str(v.pk), max_len=4)
        if seg:
            out.append(seg)
    return out


def generate_variant_sku(*, product: Product, attribute_value_ids: list[int]) -> str:
    store = getattr(product, "store", None)
    store_part = _compact_code(getattr(store, "name", ""), max_len=5) or "STORE"
    product_part = _compact_code(getattr(product, "name", ""), max_len=10) or "PRODUCT"
    parts = [store_part, product_part, *_variant_option_codes(attribute_value_ids)]
    return "-".join(p for p in parts if p)


def ensure_unique_variant_sku(*, product: Product, base_sku: str, exclude_id: int | None = None) -> str:
    sku = (base_sku or "").strip().upper()
    if not sku:
        sku = "SKU"
    qs = ProductVariant.objects.filter(product=product)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    if not qs.filter(sku=sku).exists():
        return sku
    n = 2
    while qs.filter(sku=f"{sku}-{n}").exists():
        n += 1
    return f"{sku}-{n}"


class AdminProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ['public_id', 'product', 'image', 'order']
        read_only_fields = ['public_id']

    def validate(self, attrs):
        product = attrs.get('product')
        if product is None or not getattr(product, 'pk', None):
            return attrs
        main = 1 if (product.image and getattr(product.image, 'name', None)) else 0
        if main + product.images.count() >= MAX_PRODUCT_IMAGES_TOTAL:
            raise serializers.ValidationError(
                {
                    'non_field_errors': [
                        f'Maximum {MAX_PRODUCT_IMAGES_TOTAL} images per product '
                        '(main image + gallery). Remove an image before adding another.'
                    ]
                }
            )
        return attrs


class AdminProductListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    image_url = serializers.SerializerMethodField()
    variant_count = serializers.SerializerMethodField()
    total_stock = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'public_id', 'name', 'brand', 'slug', 'price', 'original_price',
            'image_url', 'badge', 'category', 'category_name',
            'stock', 'variant_count', 'total_stock',
            'is_featured', 'is_active', 'extra_data', 'created_at',
        ]

    def get_image_url(self, obj):
        if obj.image and hasattr(obj.image, 'url'):
            return obj.image.url
        return None

    def get_variant_count(self, obj):
        n = getattr(obj, '_admin_variant_count', None)
        if n is not None:
            return int(n)
        return obj.variants.count()

    def get_total_stock(self, obj):
        """Sum variant stock when variants exist; otherwise base product.stock."""
        n = getattr(obj, '_admin_variant_count', None)
        if n is None:
            n = obj.variants.count()
        if n == 0:
            return obj.stock
        s = getattr(obj, '_admin_variant_stock_sum', None)
        if s is None:
            from django.db.models import Sum as SumAgg

            s = obj.variants.aggregate(x=SumAgg('stock_quantity'))['x']
        return int(s or 0)


class AdminProductSerializer(serializers.ModelSerializer):
    images = AdminProductImageSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    variant_count = serializers.SerializerMethodField()
    total_stock = serializers.SerializerMethodField()
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.none(),
        help_text='Leaf or intermediate category this product belongs to.',
    )

    class Meta:
        model = Product
        fields = [
            'id', 'public_id', 'name', 'brand', 'slug', 'price', 'original_price',
            'image', 'badge', 'category', 'category_name',
            'description',
            'stock', 'variant_count', 'total_stock',
            'is_featured', 'is_active', 'extra_data', 'images',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'public_id', 'slug', 'created_at', 'updated_at', 'variant_count', 'total_stock']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Category.objects.all()
        store_id = (self.context or {}).get('store_id')
        if store_id is not None:
            qs = qs.filter(store_id=store_id)
        self.fields['category'].queryset = qs

    def get_variant_count(self, obj):
        n = getattr(obj, '_admin_variant_count', None)
        if n is not None:
            return int(n)
        return obj.variants.count()

    def get_total_stock(self, obj):
        n = getattr(obj, '_admin_variant_count', None)
        if n is None:
            n = obj.variants.count()
        if n == 0:
            return obj.stock
        s = getattr(obj, '_admin_variant_stock_sum', None)
        if s is None:
            from django.db.models import Sum as SumAgg

            s = obj.variants.aggregate(x=SumAgg('stock_quantity'))['x']
        return int(s or 0)


class AdminParentCategorySerializer(serializers.ModelSerializer):
    """Serializer for top-level (parent) categories in nested hierarchy."""
    child_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            'public_id', 'name', 'slug', 'description', 'image',
            'order', 'is_active', 'child_count',
        ]
        read_only_fields = ['public_id']

    def get_child_count(self, obj):
        return obj.children.count()


class AdminCategorySerializer(serializers.ModelSerializer):
    """Serializer for child categories (nested under a parent)."""
    product_count = serializers.SerializerMethodField()
    parent = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.none(),
        allow_null=True,
        required=False,
    )
    parent_name = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            'public_id', 'name', 'slug', 'description', 'image',
            'parent', 'parent_name',
            'order', 'is_active', 'product_count',
        ]
        read_only_fields = ['public_id']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Category.objects.filter(parent__isnull=True)
        store_id = (self.context or {}).get("store_id")
        if store_id is not None:
            qs = qs.filter(store_id=store_id)
        self.fields["parent"].queryset = qs

    def get_product_count(self, obj):
        return obj.products.count()

    def get_parent_name(self, obj):
        return obj.parent.name if obj.parent else ''


class AdminProductAttributeValueSerializer(serializers.ModelSerializer):
    attribute_name = serializers.CharField(source="attribute.name", read_only=True)

    class Meta:
        model = ProductAttributeValue
        fields = ["public_id", "attribute", "attribute_name", "value", "order"]
        read_only_fields = ["public_id", "attribute_name"]


class AdminProductAttributeSerializer(serializers.ModelSerializer):
    values = AdminProductAttributeValueSerializer(many=True, read_only=True)

    class Meta:
        model = ProductAttribute
        fields = ["public_id", "name", "slug", "order", "values"]
        read_only_fields = ["public_id"]

    def validate_slug(self, value):
        if value is None:
            return value
        v = (value or "").strip()
        return v or None

    def validate(self, attrs):
        slug = attrs.get("slug")
        if slug:
            slug = slug.strip()
            attrs["slug"] = slug
            qs = ProductAttribute.objects.filter(slug=slug)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"slug": "This slug is already in use."})
        return attrs

    def create(self, validated_data):
        from django.utils.text import slugify

        slug = validated_data.get("slug")
        if not slug:
            base = slugify(validated_data["name"]) or "attribute"
            slug = base
            n = 1
            while ProductAttribute.objects.filter(slug=slug).exists():
                slug = f"{base}-{n}"
                n += 1
            validated_data["slug"] = slug
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # Slug is unique; if clearing slug on update, keep existing
        if "slug" in validated_data and not (validated_data.get("slug") or "").strip():
            validated_data.pop("slug", None)
        return super().update(instance, validated_data)


class AdminProductVariantSerializer(serializers.ModelSerializer):
    """Dashboard CRUD for variants; links attribute values via attribute_value_ids."""

    sku = serializers.CharField(required=False, allow_blank=True)
    attribute_value_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        default=list,
    )
    option_labels = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProductVariant
        fields = [
            "public_id",
            "product",
            "sku",
            "price_override",
            "stock_quantity",
            "is_active",
            "attribute_value_ids",
            "option_labels",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "option_labels", "created_at", "updated_at"]
        # DRF auto-adds UniqueTogetherValidator for the model constraint (product, sku) and
        # enforces both fields as required on create. We generate SKU when omitted, so we
        # disable auto validators and keep our own uniqueness checks + generation.
        validators: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Product.objects.all()
        store_id = (self.context or {}).get("store_id")
        if store_id is not None:
            qs = qs.filter(store_id=store_id)
        self.fields["product"] = serializers.PrimaryKeyRelatedField(queryset=qs)

    def get_option_labels(self, obj):
        links = (
            obj.attribute_values.select_related("attribute_value__attribute")
            .order_by("attribute_value__attribute__order", "attribute_value__order")
            .all()
        )
        return [
            f"{link.attribute_value.attribute.name}: {link.attribute_value.value}"
            for link in links
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["attribute_value_ids"] = [
            link.attribute_value_id for link in instance.attribute_values.all()
        ]
        return data

    def validate_attribute_value_ids(self, ids):
        if not ids:
            return []
        uniq = list(dict.fromkeys(ids))
        values = list(
            ProductAttributeValue.objects.filter(pk__in=uniq).select_related("attribute")
        )
        if len(values) != len(uniq):
            raise serializers.ValidationError("One or more attribute value ids are invalid.")
        seen_attr = set()
        for v in values:
            aid = v.attribute_id
            if aid in seen_attr:
                raise serializers.ValidationError(
                    f'Only one value per attribute allowed (duplicate for "{v.attribute.name}").'
                )
            seen_attr.add(aid)
        return uniq

    def validate(self, attrs):
        product = attrs.get("product")
        if self.instance is not None:
            product = product or self.instance.product
        sku = attrs.get("sku")
        if sku is not None:
            sku = (sku or "").strip()
            if sku:
                sku_norm = sku.upper()
                qs = ProductVariant.objects.filter(product=product, sku=sku_norm)
                if self.instance is not None:
                    qs = qs.exclude(pk=self.instance.pk)
                if qs.exists():
                    raise serializers.ValidationError(
                        {"sku": "This SKU is already used for this product."}
                    )
                attrs["sku"] = sku_norm
        return attrs

    def create(self, validated_data):
        ids = validated_data.pop("attribute_value_ids", [])
        product = validated_data["product"]
        sku = (validated_data.get("sku") or "").strip()
        if not sku:
            base = generate_variant_sku(product=product, attribute_value_ids=ids)
            validated_data["sku"] = ensure_unique_variant_sku(product=product, base_sku=base)
        variant = ProductVariant.objects.create(**validated_data)
        for pk in ids:
            ProductVariantAttribute.objects.create(
                variant=variant, attribute_value_id=pk
            )
        return variant

    def update(self, instance, validated_data):
        ids = validated_data.pop("attribute_value_ids", None)
        incoming_sku = validated_data.get("sku", None)
        if incoming_sku is not None:
            incoming_sku = (incoming_sku or "").strip().upper()

        # Auto-regenerate SKU on edit when options change, unless the user explicitly
        # provided a different SKU. This keeps SKU aligned with option edits.
        if ids is not None:
            should_regen = False
            if not incoming_sku:
                # User cleared SKU or omitted it: always generate.
                should_regen = True
            elif incoming_sku == (instance.sku or "").strip().upper():
                # SKU unchanged by user: treat as auto-managed.
                should_regen = True
            if should_regen:
                base = generate_variant_sku(product=instance.product, attribute_value_ids=ids)
                validated_data["sku"] = ensure_unique_variant_sku(
                    product=instance.product,
                    base_sku=base,
                    exclude_id=instance.pk,
                )
        for key, val in validated_data.items():
            setattr(instance, key, val)
        instance.save()
        if ids is not None:
            instance.attribute_values.all().delete()
            for pk in ids:
                ProductVariantAttribute.objects.create(
                    variant=instance, attribute_value_id=pk
                )
        return instance
