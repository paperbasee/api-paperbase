from types import SimpleNamespace

from django.db.models import Q
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response

from engine.apps.analytics.service import meta_conversions
from engine.core.tenancy import require_api_key_store, require_resolved_store

from .models import Product
from .serializers import (
    CategorySerializer,
    ProductDetailSerializer,
    ProductListSerializer,
)
from . import services


class StorefrontTenantMixin:
    """Public storefront: reject platform/anonymous requests with no tenant context."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_resolved_store(request)


class ProductListView(StorefrontTenantMixin, ListAPIView):
    """List products with optional category, brand, and featured filters."""
    serializer_class = ProductListSerializer

    def get_queryset(self):
        store = require_api_key_store(self.request)
        return services.build_product_list_queryset(store, self.request.query_params)

    def list(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        cached = services.get_cached_product_list(
            store.public_id, request.query_params
        )
        if cached is not None:
            return Response(cached)
        response = super().list(request, *args, **kwargs)
        services.set_cached_product_list(
            store.public_id, request.query_params, response.data
        )
        return response


class ProductDetailView(StorefrontTenantMixin, RetrieveAPIView):
    """Get single product by public_id (prd_xxx) or slug."""
    serializer_class = ProductDetailSerializer
    lookup_url_kwarg = 'identifier'

    def retrieve(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        identifier = self.kwargs.get(self.lookup_url_kwarg)
        data = services.get_product_detail(store, identifier, request)
        product_proxy = SimpleNamespace(
            public_id=data.get("public_id"),
            name=data.get("name"),
            price=data.get("price"),
        )
        meta_conversions.track_view_content(request, product_proxy)
        return Response(data)


class ProductRelatedView(StorefrontTenantMixin, ListAPIView):
    """Related products for a given product (same category, excluding self)."""
    serializer_class = ProductListSerializer
    pagination_class = None

    def list(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        identifier = self.kwargs.get('identifier')
        data = services.get_related_products(store, identifier, request)
        return Response(data)


class CategoryListView(StorefrontTenantMixin, ListAPIView):
    """List categories, optionally filtered by parent slug."""
    serializer_class = CategorySerializer

    def get_queryset(self):
        store = require_api_key_store(self.request)
        return services.build_category_list_queryset(
            store, self.request.query_params
        )

    def list(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        cached = services.get_cached_category_list(
            store.public_id, request.query_params
        )
        if cached is not None:
            return Response(cached)
        response = super().list(request, *args, **kwargs)
        services.set_cached_category_list(
            store.public_id, request.query_params, response.data
        )
        return response


class CategoryDetailView(StorefrontTenantMixin, RetrieveAPIView):
    """Get a single subcategory by slug."""
    serializer_class = CategorySerializer
    lookup_field = 'slug'

    def retrieve(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        slug = self.kwargs.get('slug')
        data = services.get_category_detail(store, slug, request)
        return Response(data)


class ProductSearchView(StorefrontTenantMixin, ListAPIView):
    """
    Real-time product search endpoint.
    Searches product name, brand, and description fields.
    Not cached — dynamic user input makes cache hit rates too low.
    """
    serializer_class = ProductListSerializer

    def get_queryset(self):
        store = require_api_key_store(self.request)
        query = self.request.query_params.get('q', '').strip()

        if not query or len(query) < 2:
            return Product.objects.none()

        qs = (
            Product.objects.filter(
                is_active=True, status=Product.Status.ACTIVE, store=store
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
