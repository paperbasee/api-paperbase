from types import SimpleNamespace

from django.db.models import Q
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsStorefrontAPIKey
from engine.core.http_cache import storefront_cache_headers
from engine.core.tenancy import require_api_key_store, require_resolved_store

from .models import Product
from .serializers import (
    StorefrontCategorySerializer,
    StorefrontProductDetailSerializer,
    StorefrontProductListSerializer,
)
from . import services


class StorefrontTenantMixin:
    """Public storefront: reject platform/anonymous requests with no tenant context."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_resolved_store(request)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        store = getattr(self.request, "store", None)
        if store:
            from .stock_signals import get_low_stock_threshold

            ctx["low_stock_threshold"] = get_low_stock_threshold(store)
        return ctx


class ProductListView(StorefrontTenantMixin, ListAPIView):
    """List products with optional category, brand, search, and attribute filters."""
    serializer_class = StorefrontProductListSerializer
    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

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
    list = storefront_cache_headers(max_age=60)(list)


class ProductDetailView(StorefrontTenantMixin, RetrieveAPIView):
    """Get single product by public_id (prd_xxx) or slug."""
    serializer_class = StorefrontProductDetailSerializer
    lookup_url_kwarg = 'identifier'
    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def retrieve(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        identifier = self.kwargs.get(self.lookup_url_kwarg)
        data = services.get_product_detail(store, identifier, request)
        return Response(data)
    retrieve = storefront_cache_headers(max_age=60)(retrieve)


class ProductRelatedView(StorefrontTenantMixin, ListAPIView):
    """Related products for a given product (same category, excluding self)."""
    serializer_class = StorefrontProductListSerializer
    pagination_class = None
    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def list(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        identifier = self.kwargs.get('identifier')
        data = services.get_related_products(store, identifier, request)
        return Response(data)


class CategoryListView(StorefrontTenantMixin, ListAPIView):
    """List categories, optionally filtered by parent slug."""
    serializer_class = StorefrontCategorySerializer
    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def get_queryset(self):
        store = require_api_key_store(self.request)
        return services.build_category_list_queryset(
            store, self.request.query_params
        )

    def list(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        raw_tree = (request.query_params.get("tree") or "").lower()
        if raw_tree in ("1", "true", "yes"):
            cached = services.get_cached_category_list(
                store.public_id, request.query_params
            )
            if cached is not None:
                return Response(cached)
            data = services.build_storefront_category_tree(store, request)
            services.set_cached_category_list(
                store.public_id, request.query_params, data
            )
            return Response(data)
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
    list = storefront_cache_headers(max_age=120)(list)


class CategoryDetailView(StorefrontTenantMixin, RetrieveAPIView):
    """Get a single subcategory by slug."""
    serializer_class = StorefrontCategorySerializer
    lookup_field = 'slug'
    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def retrieve(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        slug = self.kwargs.get('slug')
        data = services.get_category_detail(store, slug, request)
        return Response(data)
    retrieve = storefront_cache_headers(max_age=120)(retrieve)


class CatalogFiltersView(StorefrontTenantMixin, APIView):
    """Aggregate filter metadata for product list UI (categories, attributes, brands, price range)."""

    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def get(self, request):
        store = require_api_key_store(request)
        data = services.get_catalog_filters(store)
        return Response(data)


class ProductSearchView(StorefrontTenantMixin, ListAPIView):
    """
    Real-time product search endpoint.
    Searches product name, brand, and description fields.
    Not cached — dynamic user input makes cache hit rates too low.

    Deprecated for storefront search UX: prefer /api/v1/search/ unified payload.
    """
    serializer_class = StorefrontProductListSerializer
    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def get_queryset(self):
        store = require_api_key_store(self.request)
        query = self.request.query_params.get('q', '').strip()

        if not query or len(query) < 2:
            return Product.objects.none()

        qs = services.annotate_storefront_product_stock(
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

        return qs.order_by("name", "display_order")

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return response


class StorefrontHomeSectionsView(StorefrontTenantMixin, APIView):
    """Storefront home sections endpoint (category + first N products)."""

    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def get(self, request):
        store = require_api_key_store(request)
        try:
            limit = int(request.query_params.get("limit", "8"))
        except (TypeError, ValueError):
            limit = 8
        # Query-budget target: one category query and one product prefetch query.
        data = services.get_storefront_home_sections(store, request, limit=limit)
        return Response(data)
