from django.db import IntegrityError, transaction
from rest_framework import serializers

from engine.apps.inventory.cache_sync import sync_product_stock_cache
from engine.apps.inventory.models import Inventory
from engine.apps.inventory.utils import clamp_stock
from engine.core.serializers import SafeModelSerializer

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


class AllowBlankNullDecimalField(serializers.DecimalField):
    """Multipart forms send '' for cleared optional decimals; treat as None when allow_null."""

    def to_internal_value(self, data):
        if self.allow_null and (
            data is None
            or data == ""
            or (isinstance(data, str) and data.strip() == "")
        ):
            return None
        return super().to_internal_value(data)


class AdminProductImageSerializer(SafeModelSerializer):
    product_public_id = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=Product.objects.none(),
        source='product',
    )

    class Meta:
        model = ProductImage
        fields = ['public_id', 'product_public_id', 'image', 'order']
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Product.objects.none()
        store_id = (self.context or {}).get("store_id")
        if store_id is not None:
            qs = Product.objects.filter(store_id=store_id)
        self.fields["product_public_id"].queryset = qs


class AdminProductListSerializer(SafeModelSerializer):
    category_public_id = serializers.CharField(source='category.public_id', read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    image_url = serializers.SerializerMethodField()
    variant_count = serializers.SerializerMethodField()
    total_stock = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()
    stock_source = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'public_id', 'name', 'brand', 'slug', 'price', 'original_price',
            'image_url', 'category_public_id', 'category_name',
            'variant_count', 'total_stock', 'available_quantity', 'stock_source',
            'is_active', 'extra_data', 'created_at',
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
        """Sum variant stock when variants exist; otherwise product-level Inventory quantity."""
        n = getattr(obj, '_admin_variant_count', None)
        if n is None:
            n = obj.variants.count()
        if n == 0:
            inv = Inventory.objects.filter(product=obj, variant__isnull=True).values_list("quantity", flat=True).first()
            return int(inv or 0)
        s = getattr(obj, '_admin_variant_stock_sum', None)
        if s is None:
            from django.db.models import Sum as SumAgg

            s = obj.variants.aggregate(x=SumAgg('inventory__quantity'))['x']
        return int(s or 0)

    def get_available_quantity(self, obj):
        return self.get_total_stock(obj)

    def get_stock_source(self, obj):
        n = getattr(obj, '_admin_variant_count', None)
        if n is None:
            n = obj.variants.count()
        if n > 0:
            return "variant_inventory_sum"
        return "product_inventory"


class AdminProductSerializer(SafeModelSerializer):
    images = AdminProductImageSerializer(many=True, read_only=True)
    brand = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    original_price = AllowBlankNullDecimalField(
        max_digits=10, decimal_places=2, allow_null=True, required=False
    )
    category = serializers.SlugRelatedField(
        slug_field='public_id',
        queryset=Category.objects.none(),
        help_text='Leaf or intermediate category this product belongs to (public_id).',
    )
    category_name = serializers.CharField(source='category.name', read_only=True)
    variant_count = serializers.SerializerMethodField()
    total_stock = serializers.SerializerMethodField()
    available_quantity = serializers.SerializerMethodField()
    stock_source = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'public_id', 'name', 'brand', 'slug', 'price', 'original_price',
            'image', 'category', 'category_name',
            'description',
            'variant_count', 'total_stock', 'available_quantity', 'stock_source',
            'is_active', 'extra_data', 'images',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'public_id', 'slug', 'created_at', 'updated_at',
            'variant_count', 'total_stock', 'available_quantity', 'stock_source',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        store_id = (self.context or {}).get("store_id")
        qs = Category.objects.all()
        if store_id is not None:
            qs = qs.filter(store_id=store_id)
        self.fields["category"].queryset = qs

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
            inv = Inventory.objects.filter(product=obj, variant__isnull=True).values_list("quantity", flat=True).first()
            return int(inv or 0)
        s = getattr(obj, '_admin_variant_stock_sum', None)
        if s is None:
            from django.db.models import Sum as SumAgg

            s = obj.variants.aggregate(x=SumAgg('inventory__quantity'))['x']
        return int(s or 0)

    def get_available_quantity(self, obj):
        return self.get_total_stock(obj)

    def get_stock_source(self, obj):
        n = getattr(obj, '_admin_variant_count', None)
        if n is None:
            n = obj.variants.count()
        if n > 0:
            return "variant_inventory_sum"
        return "product_inventory"

    def validate_brand(self, value):
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def validate(self, attrs):
        if "stock" in getattr(self, "initial_data", {}):
            raise serializers.ValidationError(
                {"stock": "Direct stock mutation is disabled. Use inventory adjust endpoint."}
            )
        return super().validate(attrs)


class AdminCategorySerializer(SafeModelSerializer):
    """Admin CRUD for any category node; parent is optional (null = root)."""

    product_count = serializers.SerializerMethodField()
    child_count = serializers.SerializerMethodField()
    parent = serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=Category.objects.none(),
        allow_null=True,
        required=False,
    )
    parent_name = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "public_id",
            "name",
            "slug",
            "description",
            "image",
            "parent",
            "parent_name",
            "order",
            "is_active",
            "product_count",
            "child_count",
        ]
        read_only_fields = ["public_id", "slug"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        store_id = (self.context or {}).get("store_id")
        qs = Category.objects.all()
        if store_id is not None:
            qs = qs.filter(store_id=store_id)
        instance = self.instance
        if instance is not None and getattr(instance, "pk", None):
            from .category_tree import excluded_parent_pks_for_category

            qs = qs.exclude(pk__in=excluded_parent_pks_for_category(instance))
        self.fields["parent"].queryset = qs

    def get_product_count(self, obj):
        n = getattr(obj, "_pc", None)
        if n is not None:
            return int(n)
        return obj.products.count()

    def get_child_count(self, obj):
        n = getattr(obj, "_child_count", None)
        if n is not None:
            return int(n)
        return obj.children.count()

    def get_parent_name(self, obj):
        return obj.parent.name if obj.parent else ""

    def validate_parent(self, value):
        if value == "":
            return None
        return value

    def validate(self, attrs):
        from .category_tree import validate_category_parent

        store_id = (self.context or {}).get("store_id")
        if store_id is None:
            return super().validate(attrs)
        if "parent" in attrs:
            parent = attrs["parent"]
        elif self.instance is not None:
            parent = self.instance.parent
        else:
            parent = None
        validate_category_parent(
            instance_pk=self.instance.pk if self.instance else None,
            store_id=store_id,
            parent=parent,
        )
        return super().validate(attrs)


class AdminProductAttributeValueSerializer(SafeModelSerializer):
    attribute = serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=ProductAttribute.objects.none(),
    )
    attribute_name = serializers.CharField(source="attribute.name", read_only=True)

    class Meta:
        model = ProductAttributeValue
        fields = ["public_id", "attribute", "attribute_name", "value", "order"]
        read_only_fields = ["public_id", "attribute_name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        store_id = (self.context or {}).get("store_id")
        if store_id is not None:
            self.fields["attribute"].queryset = ProductAttribute.objects.filter(store_id=store_id)


class AdminProductAttributeSerializer(SafeModelSerializer):
    values = AdminProductAttributeValueSerializer(many=True, read_only=True)

    class Meta:
        model = ProductAttribute
        fields = ["public_id", "name", "slug", "order", "values"]
        read_only_fields = ["public_id", "slug"]


class AdminProductVariantSerializer(SafeModelSerializer):
    """Dashboard CRUD for variants; links attribute values via attribute_value_public_ids."""

    sku = serializers.CharField(read_only=True)
    attribute_value_public_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
    )
    option_labels = serializers.SerializerMethodField(read_only=True)
    available_quantity = serializers.SerializerMethodField(read_only=True)
    stock_source = serializers.SerializerMethodField(read_only=True)
    effective_price = serializers.SerializerMethodField(read_only=True)
    product_public_id = serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=Product.objects.all(),
        source="product",
    )

    class Meta:
        model = ProductVariant
        fields = [
            "public_id",
            "product_public_id",
            "sku",
            "price_override",
            "effective_price",
            "is_active",
            "attribute_value_public_ids",
            "option_labels",
            "available_quantity",
            "stock_source",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "public_id",
            "sku",
            "option_labels",
            "available_quantity",
            "stock_source",
            "effective_price",
            "created_at",
            "updated_at",
        ]
        validators: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Product.objects.all()
        store_id = (self.context or {}).get("store_id")
        if store_id is not None:
            qs = qs.filter(store_id=store_id)
        self.fields["product_public_id"].queryset = qs

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

    def get_available_quantity(self, obj):
        inv = getattr(obj, "inventory", None)
        if inv is None:
            return 0
        return int(inv.quantity or 0)

    def get_stock_source(self, obj):
        return "variant_inventory"

    def get_effective_price(self, obj):
        return str(obj.effective_price)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["attribute_value_public_ids"] = [
            link.attribute_value.public_id for link in instance.attribute_values.select_related("attribute_value").all()
        ]
        return data

    def validate_attribute_value_public_ids(self, public_ids):
        if not public_ids:
            return []
        uniq = list(dict.fromkeys(public_ids))
        store_id = (self.context or {}).get("store_id")
        value_qs = ProductAttributeValue.objects.filter(public_id__in=uniq).select_related("attribute")
        if store_id is not None:
            value_qs = value_qs.filter(store_id=store_id)
        values = list(value_qs)
        if len(values) != len(uniq):
            raise serializers.ValidationError("One or more attribute value public_ids are invalid.")
        seen_attr = set()
        for v in values:
            aid = v.attribute_id
            if aid in seen_attr:
                raise serializers.ValidationError(
                    f'Only one value per attribute allowed (duplicate for "{v.attribute.name}").'
                )
            seen_attr.add(aid)
        return uniq

    def _resolve_attribute_values(self, public_ids: list[str]) -> list[ProductAttributeValue]:
        """Resolve a list of attribute value public_ids to model instances."""
        if not public_ids:
            return []
        store_id = (self.context or {}).get("store_id")
        value_qs = ProductAttributeValue.objects.filter(public_id__in=public_ids)
        if store_id is not None:
            value_qs = value_qs.filter(store_id=store_id)
        values = list(value_qs)
        return values

    def create(self, validated_data):
        public_ids = validated_data.pop("attribute_value_public_ids", [])
        validated_data.pop("sku", None)
        try:
            with transaction.atomic():
                variant = ProductVariant.objects.create(**validated_data)
        except IntegrityError as e:
            raise serializers.ValidationError(
                {"non_field_errors": ["Could not create variant (SKU conflict). Retry the request."]}
            ) from e
        attr_values = self._resolve_attribute_values(public_ids)
        for av in attr_values:
            ProductVariantAttribute.objects.create(variant=variant, attribute_value=av)
        is_first_variant = (
            ProductVariant.objects.filter(product_id=variant.product_id).count() == 1
        )
        transferred_qty = 0
        if is_first_variant:
            old_inv = Inventory.objects.filter(
                product=variant.product, variant__isnull=True
            ).first()
            if old_inv:
                transferred_qty = clamp_stock(old_inv.quantity)
                Inventory.objects.filter(
                    product=variant.product, variant__isnull=True
                ).delete()
        Inventory.objects.get_or_create(
            product=variant.product,
            variant=variant,
            defaults={"quantity": clamp_stock(transferred_qty)},
        )
        if is_first_variant:
            sync_product_stock_cache(int(variant.product.store_id))
        return variant

    def update(self, instance, validated_data):
        public_ids = validated_data.pop("attribute_value_public_ids", None)
        validated_data.pop("sku", None)
        for key, val in validated_data.items():
            setattr(instance, key, val)
        instance.save()
        if public_ids is not None:
            instance.attribute_values.all().delete()
            attr_values = self._resolve_attribute_values(public_ids)
            for av in attr_values:
                ProductVariantAttribute.objects.create(variant=instance, attribute_value=av)
        return instance
