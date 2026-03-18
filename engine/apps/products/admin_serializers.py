from rest_framework import serializers

from .models import Brand, Category, Product, ProductImage


class AdminProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ['id', 'product', 'image', 'order']
        read_only_fields = ['id']


class AdminProductListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'brand', 'slug', 'price', 'original_price',
            'image_url', 'badge', 'category', 'category_name',
            'stock',
            'is_featured', 'is_active', 'created_at',
        ]

    def get_image_url(self, obj):
        if obj.image and hasattr(obj.image, 'url'):
            return obj.image.url
        return None


class AdminProductSerializer(serializers.ModelSerializer):
    images = AdminProductImageSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'brand', 'slug', 'price', 'original_price',
            'image', 'badge', 'category', 'category_name',
            'description',
            'stock', 'is_featured', 'is_active', 'images',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']


class AdminParentCategorySerializer(serializers.ModelSerializer):
    """Serializer for top-level (parent) categories in nested hierarchy."""
    child_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            'id', 'name', 'slug', 'description', 'image',
            'order', 'is_active', 'child_count',
        ]
        read_only_fields = ['id']

    def get_child_count(self, obj):
        return obj.children.count()


class AdminCategorySerializer(serializers.ModelSerializer):
    """Serializer for child categories (nested under a parent)."""
    product_count = serializers.SerializerMethodField()
    parent = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.filter(parent__isnull=True),
        allow_null=True,
        required=False,
    )
    parent_name = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            'id', 'name', 'slug', 'description', 'image',
            'parent', 'parent_name',
            'order', 'is_active', 'product_count',
        ]
        read_only_fields = ['id']

    def get_product_count(self, obj):
        return obj.products.count()

    def get_parent_name(self, obj):
        return obj.parent.name if obj.parent else ''


class AdminBrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = [
            'id', 'name', 'slug', 'image', 'redirect_url',
            'brand_type', 'order', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
