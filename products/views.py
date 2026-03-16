from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from meta_pixel.service import meta_conversions

from .models import Brand, Category, NavbarCategory, Product
from .serializers import (
    BrandSerializer,
    CategorySerializer,
    NavbarCategorySerializer,
    SubcategorySerializer,
    ProductDetailSerializer,
    ProductListSerializer,
)


class ProductListView(ListAPIView):
    """List products with optional category, subcategory, brand, and featured filters."""
    serializer_class = ProductListSerializer

    def get_queryset(self):
        qs = Product.objects.filter(is_active=True).select_related('category', 'sub_category').prefetch_related('images')
        category = self.request.query_params.get('category')
        subcategory = self.request.query_params.get('subcategory')
        brand = self.request.query_params.get('brand')

        if category:
            # Support comma-separated navbar category slugs
            category_slugs = [c.strip() for c in category.split(',') if c.strip()]
            if category_slugs:
                qs = qs.filter(category__slug__in=category_slugs)

        if subcategory:
            qs = qs.filter(sub_category__slug=subcategory)

        if brand:
            brands = [b.strip() for b in brand.split(',') if b.strip()]
            if brands:
                qs = qs.filter(brand__in=brands)

        featured = self.request.query_params.get('featured')
        if featured and featured.lower() == 'true':
            qs = qs.filter(is_featured=True)

        hot_deals = self.request.query_params.get('hot_deals')
        if hot_deals and hot_deals.lower() == 'true':
            qs = qs.filter(badge='sale')

        return qs


class ProductDetailView(RetrieveAPIView):
    """Get single product by UUID or slug."""
    serializer_class = ProductDetailSerializer
    queryset = Product.objects.filter(is_active=True).select_related('category', 'sub_category').prefetch_related('images')
    lookup_url_kwarg = 'identifier'

    def get_object(self):
        identifier = self.kwargs.get(self.lookup_url_kwarg)
        qs = self.get_queryset()
        try:
            import uuid

            uuid.UUID(str(identifier))
            return get_object_or_404(qs, id=identifier)
        except Exception:
            return get_object_or_404(qs, slug=identifier)

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        product = self.get_object()
        meta_conversions.track_view_content(request, product)
        return response


class ProductRelatedView(ListAPIView):
    """Related products for a given product (same category, excluding self)."""
    serializer_class = ProductListSerializer

    def get_queryset(self):
        identifier = self.kwargs.get('identifier')
        qs = Product.objects.filter(is_active=True)
        try:
            import uuid

            uuid.UUID(str(identifier))
            product = get_object_or_404(qs, id=identifier)
        except Exception:
            product = get_object_or_404(qs, slug=identifier)
        return (
            Product.objects.filter(is_active=True, category=product.category)
            .exclude(id=product.id)
            .select_related('category', 'sub_category')
            .prefetch_related('images')[:4]
        )


class NavbarCategoryListView(ListAPIView):
    """
    List all active navbar categories with their subcategories.
    Used by the frontend for navigation and category pages.
    """
    serializer_class = NavbarCategorySerializer

    def get_queryset(self):
        return NavbarCategory.objects.filter(
            is_active=True
        ).prefetch_related('subcategories')


class NavbarCategoryDetailView(RetrieveAPIView):
    """Get a single navbar category by slug, including its subcategories."""
    serializer_class = NavbarCategorySerializer
    lookup_field = 'slug'

    def get_queryset(self):
        return NavbarCategory.objects.filter(is_active=True).prefetch_related('subcategories')


class CategoryListView(ListAPIView):
    """
    List subcategories, optionally filtered by navbar category slug.
    Pass ?navbar_category=<slug> to get subcategories for a specific navbar category.
    """
    serializer_class = CategorySerializer

    def get_queryset(self):
        qs = Category.objects.filter(is_active=True).select_related('navbar_category')
        navbar_slug = self.request.query_params.get('navbar_category')
        if navbar_slug:
            qs = qs.filter(navbar_category__slug=navbar_slug)
        return qs


class CategoryDetailView(RetrieveAPIView):
    """Get a single subcategory by slug."""
    serializer_class = CategorySerializer
    lookup_field = 'slug'

    def get_queryset(self):
        return Category.objects.filter(is_active=True).select_related('navbar_category')


class SubcategoryListView(ListAPIView):
    """List subcategories for a given navbar category slug."""
    serializer_class = SubcategorySerializer

    def get_queryset(self):
        parent_slug = self.kwargs.get('parent_slug')
        return Category.objects.filter(
            navbar_category__slug=parent_slug,
            is_active=True
        ).order_by('order', 'name')


class BrandListView(APIView):
    """
    List all unique product brands, optionally filtered by navbar category.
    Returns brand names sorted alphabetically.
    """
    def get(self, request):
        category_slug = request.query_params.get('category')

        qs = Product.objects.filter(is_active=True)

        if category_slug:
            qs = qs.filter(category__slug=category_slug)

        brands = qs.values_list('brand', flat=True).distinct().order_by('brand')

        return Response(list(brands))


class BrandShowcaseView(APIView):
    """
    List all active brands for the homepage showcase.
    Can filter by brand_type (accessories, gadgets) using query parameter.
    """
    def get(self, request):
        brand_type = request.query_params.get('type')

        qs = Brand.objects.filter(is_active=True)

        if brand_type:
            qs = qs.filter(brand_type=brand_type)

        serializer = BrandSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)


class ProductSearchView(ListAPIView):
    """
    Real-time product search endpoint.
    Searches product name, brand, and description fields.
    """
    serializer_class = ProductListSerializer

    def get_queryset(self):
        query = self.request.query_params.get('q', '').strip()

        if not query or len(query) < 2:
            return Product.objects.none()

        qs = Product.objects.filter(is_active=True).select_related(
            'category', 'sub_category'
        ).prefetch_related('images')

        qs = qs.filter(
            Q(name__icontains=query) |
            Q(brand__icontains=query) |
            Q(description__icontains=query)
        )

        return qs.order_by('name')[:10]

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        query = request.query_params.get('q', '').strip()
        if query and len(query) >= 2:
            meta_conversions.track_search(request, query)
        return response
