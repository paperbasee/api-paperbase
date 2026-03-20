from django.db.models import Count, Prefetch, Q, Sum
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from engine.apps.analytics.service import meta_conversions
from engine.core.tenancy import get_active_store

from .models import Category, Product, ProductVariant, ProductVariantAttribute
from .serializers import (
    CategorySerializer,
    ProductDetailSerializer,
    ProductListSerializer,
)


class ProductListView(ListAPIView):
    """List products with optional category, subcategory, brand, and featured filters."""
    serializer_class = ProductListSerializer

    def get_queryset(self):
        ctx = get_active_store(self.request)
        qs = Product.objects.filter(
            store=ctx.store,
            is_active=True,
            status=Product.Status.ACTIVE,
        ).select_related("category").prefetch_related("images").annotate(
            _pub_variant_count=Count("variants", filter=Q(variants__is_active=True)),
            _pub_variant_stock_sum=Sum(
                "variants__stock_quantity", filter=Q(variants__is_active=True)
            ),
        )
        category = self.request.query_params.get('category')
        brand = self.request.query_params.get('brand')

        if category:
            # Support comma-separated category slugs
            category_slugs = [c.strip() for c in category.split(',') if c.strip()]
            if category_slugs:
                qs = qs.filter(category__slug__in=category_slugs)

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

        return qs.order_by("-created_at", "id")


class ProductDetailView(RetrieveAPIView):
    """Get single product by UUID or slug."""
    serializer_class = ProductDetailSerializer
    def get_queryset(self):
        ctx = get_active_store(self.request)
        active_variant_qs = ProductVariant.objects.filter(is_active=True).prefetch_related(
            Prefetch(
                "attribute_values",
                queryset=ProductVariantAttribute.objects.select_related(
                    "attribute_value__attribute"
                ),
            )
        )
        return (
            Product.objects.filter(
                store=ctx.store,
                is_active=True,
                status=Product.Status.ACTIVE,
            )
            .select_related("category")
            .prefetch_related("images", Prefetch("variants", queryset=active_variant_qs))
            .annotate(
                _pub_variant_count=Count("variants", filter=Q(variants__is_active=True)),
                _pub_variant_stock_sum=Sum(
                    "variants__stock_quantity", filter=Q(variants__is_active=True)
                ),
            )
        )
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
        ctx = get_active_store(self.request)
        if not ctx.store:
            return Product.objects.none()
        identifier = self.kwargs.get('identifier')
        base_qs = Product.objects.filter(
            is_active=True, status=Product.Status.ACTIVE, store=ctx.store
        )
        try:
            import uuid

            uuid.UUID(str(identifier))
            product = get_object_or_404(base_qs, id=identifier)
        except Exception:
            product = get_object_or_404(base_qs, slug=identifier)
        return (
            Product.objects.filter(
                is_active=True,
                status=Product.Status.ACTIVE,
                store=ctx.store,
                category=product.category,
            )
            .exclude(id=product.id)
            .select_related("category")
            .prefetch_related("images")
            .annotate(
                _pub_variant_count=Count("variants", filter=Q(variants__is_active=True)),
                _pub_variant_stock_sum=Sum(
                    "variants__stock_quantity", filter=Q(variants__is_active=True)
                ),
            )
            .order_by("-created_at", "id")[:4]
        )


class CategoryListView(ListAPIView):
    """List categories, optionally filtered by parent slug."""
    serializer_class = CategorySerializer

    def get_queryset(self):
        ctx = get_active_store(self.request)
        qs = Category.objects.filter(
            store=ctx.store,
            is_active=True,
        )
        parent_slug = self.request.query_params.get('parent')
        if parent_slug:
            parent = get_object_or_404(
                Category.objects.filter(store=ctx.store, is_active=True),
                slug=parent_slug,
            )
            qs = qs.filter(parent=parent)
        else:
            qs = qs.filter(parent__isnull=True)
        return qs


class CategoryDetailView(RetrieveAPIView):
    """Get a single subcategory by slug."""
    serializer_class = CategorySerializer
    lookup_field = 'slug'

    def get_queryset(self):
        ctx = get_active_store(self.request)
        return Category.objects.filter(
            store=ctx.store,
            is_active=True,
        )


class BrandListView(APIView):
    """
    List all unique product brands, optionally filtered by navbar category.
    Returns brand names sorted alphabetically.
    """
    def get(self, request):
        ctx = get_active_store(request)
        if not ctx.store:
            return Response([])

        category_slug = request.query_params.get('category')

        qs = Product.objects.filter(
            is_active=True, status=Product.Status.ACTIVE, store=ctx.store
        )

        if category_slug:
            qs = qs.filter(category__slug=category_slug)

        brands = qs.values_list('brand', flat=True).distinct().order_by('brand')

        return Response(list(brands))


class BrandShowcaseView(APIView):
    """
    List all active brands for the homepage showcase from Store.brand_showcase.
    Can filter by brand_type (accessories, gadgets) using query parameter.
    """
    def get(self, request):
        ctx = get_active_store(request)
        store = ctx.store
        if not store:
            return Response([])

        showcase = getattr(store, "brand_showcase", []) or []
        brand_type = request.query_params.get("type")

        result = []
        for item in showcase:
            if not item.get("is_active", True):
                continue
            if brand_type and item.get("brand_type") != brand_type:
                continue
            entry = dict(item)
            img = entry.get("image_url")
            if img and not img.startswith(("http://", "https://")):
                entry["image_url"] = request.build_absolute_uri(img) if request else img
            result.append(entry)

        result.sort(key=lambda x: (x.get("brand_type", ""), x.get("order", 0), x.get("name", "")))
        return Response(result)


class ProductSearchView(ListAPIView):
    """
    Real-time product search endpoint.
    Searches product name, brand, and description fields.
    """
    serializer_class = ProductListSerializer

    def get_queryset(self):
        ctx = get_active_store(self.request)
        if not ctx.store:
            return Product.objects.none()

        query = self.request.query_params.get('q', '').strip()

        if not query or len(query) < 2:
            return Product.objects.none()

        qs = (
            Product.objects.filter(
                is_active=True, status=Product.Status.ACTIVE, store=ctx.store
            )
            .select_related('category')
            .prefetch_related('images')
        )

        qs = qs.filter(
            Q(name__icontains=query) |
            Q(brand__icontains=query) |
            Q(description__icontains=query)
        )

        return qs.order_by('name', 'id')[:10]

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        query = request.query_params.get('q', '').strip()
        if query and len(query) >= 2:
            meta_conversions.track_search(request, query)
        return response
