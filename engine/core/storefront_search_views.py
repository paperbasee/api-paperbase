from __future__ import annotations

from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsStorefrontAPIKey
from engine.apps.analytics.service import meta_conversions
from engine.apps.products.models import Category, Product
from engine.apps.products.serializers import (
    StorefrontCategorySerializer,
    StorefrontProductListSerializer,
)
from engine.apps.products.services import annotate_storefront_product_stock, build_product_list_queryset
from engine.apps.products.stock_signals import get_low_stock_threshold
from engine.core.tenancy import require_api_key_store, require_resolved_store


class StorefrontSearchView(APIView):
    """
    Storefront search: product hits, category hits, name suggestions, optional trending.
    Query: q (min 2 chars), trending=true (popular products, ignores q).
    """

    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_resolved_store(request)

    def get(self, request):
        store = require_api_key_store(request)
        threshold = get_low_stock_threshold(store)
        ctx = {"request": request, "low_stock_threshold": threshold}
        trending_raw = (request.query_params.get("trending") or "").lower()
        trending = trending_raw in ("1", "true", "yes")

        empty = {
            "products": [],
            "categories": [],
            "suggestions": [],
            "trending": trending,
        }

        if trending:
            qs = build_product_list_queryset(store, {"ordering": "popularity"})
            product_rows = list(qs[:12])
            empty["products"] = StorefrontProductListSerializer(
                product_rows, many=True, context=ctx
            ).data
            return Response(empty)

        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            empty["trending"] = False
            return Response(empty)

        meta_conversions.track_search(request, q)

        prod_qs = (
            annotate_storefront_product_stock(
                Product.objects.filter(
                    store=store,
                    is_active=True,
                    status=Product.Status.ACTIVE,
                )
                .filter(
                    Q(name__icontains=q)
                    | Q(brand__icontains=q)
                    | Q(description__icontains=q)
                )
                .select_related("category")
                .prefetch_related("images")
            )
            .order_by("name", "id")[:10]
        )
        product_rows = list(prod_qs)
        products = StorefrontProductListSerializer(
            product_rows, many=True, context=ctx
        ).data

        cat_qs = list(
            Category.objects.filter(store=store, is_active=True, name__icontains=q)
            .order_by("name", "id")[:8]
        )
        categories = StorefrontCategorySerializer(
            cat_qs, many=True, context={"request": request}
        ).data

        suggestions: list[str] = []
        for row in product_rows:
            if row.name and row.name not in suggestions:
                suggestions.append(row.name)
            if len(suggestions) >= 10:
                break
        for c in cat_qs:
            if c.name not in suggestions:
                suggestions.append(c.name)
            if len(suggestions) >= 10:
                break

        return Response(
            {
                "products": products,
                "categories": categories,
                "suggestions": suggestions[:10],
                "trending": False,
            }
        )
