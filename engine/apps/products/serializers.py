from rest_framework import serializers

from .models import (
    Category,
    Product,
    ProductImage,
    ProductVariant,
    ProductVariantAttribute,
)


def _image_url(img, request):
    if not img:
        return None
    return request.build_absolute_uri(img.url) if request else img.url


class ProductImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ['public_id', 'url', 'order']

    def get_url(self, obj):
        return _image_url(obj.image, self.context.get('request'))


class ProductVariantPublicSerializer(serializers.ModelSerializer):
    """Storefront: one sellable SKU with options (color/size/etc.)."""
    price = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = ['public_id', 'sku', 'stock_quantity', 'is_active', 'price', 'options']

    def get_price(self, obj):
        return str(obj.effective_price)

    def get_options(self, obj):
        rows = []
        for link in obj.attribute_values.select_related('attribute_value__attribute').all():
            av = link.attribute_value
            rows.append({'attribute': av.attribute.name, 'value': av.value})
        return rows


class ProductListSerializer(serializers.ModelSerializer):
    """For list views: matches frontend Product shape."""
    image = serializers.SerializerMethodField()
    originalPrice = serializers.DecimalField(
        source='original_price', max_digits=10, decimal_places=2,
        read_only=True, allow_null=True
    )
    totalStock = serializers.SerializerMethodField()
    variantCount = serializers.SerializerMethodField()
    # Return category slug for frontend URL generation
    category = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Product
        fields = [
            'public_id', 'name', 'brand', 'price', 'originalPrice', 'image',
            'badge', 'category', 'slug', 'stock', 'totalStock', 'variantCount', 'extra_data',
        ]

    def get_image(self, obj):
        return _image_url(obj.image, self.context.get('request'))

    def get_variantCount(self, obj):
        n = getattr(obj, '_pub_variant_count', None)
        if n is not None:
            return int(n)
        return obj.variants.filter(is_active=True).count()

    def get_totalStock(self, obj):
        """In-stock units: sum active variant stock, else base product.stock."""
        n = getattr(obj, '_pub_variant_count', None)
        if n is None:
            n = obj.variants.filter(is_active=True).count()
        if n == 0:
            return obj.stock
        s = getattr(obj, '_pub_variant_stock_sum', None)
        if s is None:
            from django.db.models import Sum as SumAgg

            s = obj.variants.filter(is_active=True).aggregate(x=SumAgg('stock_quantity'))['x']
        return int(s or 0)


class ProductDetailSerializer(serializers.ModelSerializer):
    """For detail view: adds images, description, variants, aggregated stock."""
    image = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    originalPrice = serializers.DecimalField(
        source='original_price', max_digits=10, decimal_places=2,
        read_only=True, allow_null=True
    )
    totalStock = serializers.SerializerMethodField()
    variantCount = serializers.SerializerMethodField()
    variants = ProductVariantPublicSerializer(many=True, read_only=True)
    # Return category slug for frontend compatibility
    category = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Product
        fields = [
            'public_id', 'name', 'brand', 'slug', 'price', 'originalPrice', 'image', 'images',
            'badge', 'category', 'description',
            'is_featured', 'created_at', 'stock', 'totalStock', 'variantCount', 'variants',
            'extra_data',
        ]

    def get_image(self, obj):
        return _image_url(obj.image, self.context.get('request'))

    def get_images(self, obj):
        qs = obj.images.all()
        req = self.context.get('request')
        return [_image_url(i.image, req) for i in qs] if qs.exists() else []

    def get_variantCount(self, obj):
        n = getattr(obj, '_pub_variant_count', None)
        if n is not None:
            return int(n)
        return obj.variants.filter(is_active=True).count()

    def get_totalStock(self, obj):
        n = getattr(obj, '_pub_variant_count', None)
        if n is None:
            n = obj.variants.filter(is_active=True).count()
        if n == 0:
            return obj.stock
        s = getattr(obj, '_pub_variant_stock_sum', None)
        if s is None:
            from django.db.models import Sum as SumAgg

            s = obj.variants.filter(is_active=True).aggregate(x=SumAgg('stock_quantity'))['x']
        return int(s or 0)

class CategorySerializer(serializers.ModelSerializer):
    """Serializer for category tree nodes."""
    image = serializers.SerializerMethodField()
    parent_public_id = serializers.CharField(source="parent.public_id", read_only=True, allow_null=True)

    class Meta:
        model = Category
        fields = ['public_id', 'name', 'slug', 'image', 'parent_public_id', 'order']

    def get_image(self, obj):
        return _image_url(obj.image, self.context.get('request'))
