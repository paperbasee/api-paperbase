"""Full-cart storefront pricing (merchandise subtotal + shipping)."""

from django.db.models import Prefetch
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from engine.core import cache_service
from config.permissions import IsStorefrontAPIKey
from engine.apps.products.models import Product, ProductVariant
from engine.apps.products.variant_utils import resolve_storefront_variant, unit_price_for_line
from engine.apps.shipping.models import ShippingMethod, ShippingZone
from engine.core.tenancy import require_api_key_store

from .pricing import PricingEngine, storefront_pricing_breakdown_response


class PricingBreakdownView(APIView):
    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True

    def post(self, request):
        store = require_api_key_store(request)
        items = request.data.get("items") or []
        if not isinstance(items, list) or not items:
            return Response({"items": "At least one item is required."}, status=status.HTTP_400_BAD_REQUEST)
        shipping_zone_public_id = (request.data.get("shipping_zone_public_id") or "").strip()
        shipping_method_public_id = (request.data.get("shipping_method_public_id") or "").strip()
        if not shipping_zone_public_id:
            return Response(
                {"shipping_zone_public_id": "This field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        normalized_items = []
        for item in items:
            normalized_items.append(
                (
                    str(item.get("product_public_id", "")).strip(),
                    str(item.get("variant_public_id", "")).strip(),
                    int(item.get("quantity") or 0),
                )
            )
        cache_params = {
            "store_public_id": store.public_id,
            "items": sorted(normalized_items),
            "shipping_zone_public_id": shipping_zone_public_id,
            "shipping_method_public_id": shipping_method_public_id,
        }
        cache_hash = cache_service.hash_params(cache_params)
        cache_key = cache_service.build_key(store.public_id, "pricing_breakdown", cache_hash)

        product_public_ids = [product_public_id for product_public_id, _variant_public_id, _qty in normalized_items]
        products = {
            p.public_id: p
            for p in Product.objects.filter(
                store=store,
                public_id__in=product_public_ids,
                is_active=True,
                status=Product.Status.ACTIVE,
            )
            .select_related("category", "category__parent")
            .prefetch_related(
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(is_active=True).select_related("product"),
                    to_attr="active_variants_prefetched",
                )
            )
        }
        pricing_lines = []
        for public_id, variant_public_id, quantity in normalized_items:
            product = products.get(public_id)
            if not product or quantity <= 0:
                return Response({"items": "Invalid product_public_id or quantity."}, status=status.HTTP_400_BAD_REQUEST)
            try:
                variant = resolve_storefront_variant(
                    product=product,
                    variant_public_id=variant_public_id,
                )
            except serializers.ValidationError as exc:
                return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
            unit_price = unit_price_for_line(product, variant)
            pricing_lines.append(
                {"product": product, "quantity": quantity, "unit_price": unit_price}
            )

        zone = ShippingZone.objects.filter(store=store, public_id=shipping_zone_public_id, is_active=True).first()
        if zone is None:
            return Response(
                {"shipping_zone_public_id": "Invalid or inactive shipping zone."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        method = None
        if shipping_method_public_id:
            method = ShippingMethod.objects.filter(
                store=store, public_id=shipping_method_public_id, is_active=True
            ).first()

        def fetcher():
            breakdown = PricingEngine.compute(
                store=store,
                lines=pricing_lines,
                shipping_zone_pk=zone.id,
                shipping_method_pk=method.id if method else None,
                resolved_shipping_zone=zone,
            )
            return storefront_pricing_breakdown_response(breakdown)

        payload = cache_service.get_or_set(cache_key, fetcher, 30)
        return Response(
            payload,
            status=status.HTTP_200_OK,
        )
