from rest_framework import serializers

from .models import Brand, Category, NavbarCategory, Product, ProductImage


class AdminProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ['id', 'product', 'image', 'order']
        read_only_fields = ['id']


class AdminProductListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    sub_category_name = serializers.CharField(
        source='sub_category.name', read_only=True, default=None,
    )
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'brand', 'slug', 'price', 'original_price',
            'image_url', 'badge', 'category', 'category_name',
            'sub_category', 'sub_category_name', 'stock',
            'is_featured', 'is_active', 'created_at',
        ]

    def get_image_url(self, obj):
        if obj.image and hasattr(obj.image, 'url'):
            return obj.image.url
        return None


class AdminProductSerializer(serializers.ModelSerializer):
    images = AdminProductImageSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True)
    sub_category_name = serializers.CharField(
        source='sub_category.name', read_only=True, default=None,
    )

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'brand', 'slug', 'price', 'original_price',
            'image', 'badge', 'category', 'category_name',
            'sub_category', 'sub_category_name', 'description',
            'stock', 'is_featured', 'is_active', 'images',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']


class AdminNavbarCategorySerializer(serializers.ModelSerializer):
    subcategory_count = serializers.SerializerMethodField()

    class Meta:
        model = NavbarCategory
        fields = [
            'id', 'name', 'slug', 'description', 'image',
            'order', 'is_active', 'subcategory_count',
        ]
        read_only_fields = ['id']

    def get_subcategory_count(self, obj):
        return obj.subcategories.count()


class AdminCategorySerializer(serializers.ModelSerializer):
    navbar_category_name = serializers.CharField(
        source='navbar_category.name', read_only=True,
    )
    product_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            'id', 'name', 'slug', 'description', 'image',
            'navbar_category', 'navbar_category_name',
            'order', 'is_active', 'product_count',
        ]
        read_only_fields = ['id']

    def get_product_count(self, obj):
        return obj.subcategory_products.count()


class AdminBrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = [
            'id', 'name', 'slug', 'image', 'redirect_url',
            'brand_type', 'order', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
