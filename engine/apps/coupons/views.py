from decimal import Decimal

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsStorefrontAPIKey
from engine.apps.orders.pricing import PricingEngine
from engine.apps.products.models import Product
from engine.apps.shipping.models import ShippingMethod, ShippingZone
from engine.core.tenancy import require_api_key_store

from .services import validate_coupon_for_subtotal


class CouponApplyInputSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=50)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)

    def validate_subtotal(self, value):
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("Subtotal must be greater than zero.")
        return value


class CouponApplyView(APIView):
    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True

    def post(self, request):
        store = require_api_key_store(request)
        serializer = CouponApplyInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quote = validate_coupon_for_subtotal(
            store=store,
            code=serializer.validated_data["code"],
            subtotal=serializer.validated_data["subtotal"],
            user=request.user if request.user.is_authenticated else None,
        )
        return Response(
            {
                "coupon_public_id": quote.coupon.public_id,
                "code": quote.coupon.code,
                "discount_type": quote.coupon.discount_type,
                "discount_value": quote.coupon.discount_value,
                "discount_amount": quote.discount_amount,
                "subtotal": serializer.validated_data["subtotal"],
                "subtotal_after_discount": serializer.validated_data["subtotal"] - quote.discount_amount,
            },
            status=status.HTTP_200_OK,
        )


class PricingBreakdownView(APIView):
    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True

    def post(self, request):
        store = require_api_key_store(request)
        items = request.data.get("items") or []
        if not isinstance(items, list) or not items:
            return Response({"items": "At least one item is required."}, status=status.HTTP_400_BAD_REQUEST)
        product_public_ids = [str(item.get("product_public_id", "")).strip() for item in items]
        products = {
            p.public_id: p
            for p in Product.objects.filter(
                store=store,
                public_id__in=product_public_ids,
                is_active=True,
                status=Product.Status.ACTIVE,
            ).select_related("category", "category__parent")
        }
        pricing_lines = []
        for item in items:
            public_id = str(item.get("product_public_id", "")).strip()
            quantity = int(item.get("quantity") or 0)
            product = products.get(public_id)
            if not product or quantity <= 0:
                return Response({"items": "Invalid product_public_id or quantity."}, status=status.HTTP_400_BAD_REQUEST)
            pricing_lines.append({"product": product, "quantity": quantity, "unit_price": product.price})

        shipping_zone_public_id = (request.data.get("shipping_zone_public_id") or "").strip()
        shipping_method_public_id = (request.data.get("shipping_method_public_id") or "").strip()
        zone = ShippingZone.objects.filter(store=store, public_id=shipping_zone_public_id, is_active=True).first()
        method = None
        if shipping_method_public_id:
            method = ShippingMethod.objects.filter(
                store=store, public_id=shipping_method_public_id, is_active=True
            ).first()

        breakdown = PricingEngine.compute(
            store=store,
            lines=pricing_lines,
            coupon_code=(request.data.get("coupon_code") or "").strip(),
            user=request.user if request.user.is_authenticated else None,
            shipping_zone_id=zone.id if zone else None,
            shipping_method_id=method.id if method else None,
        )
        return Response(
            {
                "base_subtotal": breakdown.base_subtotal,
                "bulk_discount_total": breakdown.bulk_discount_total,
                "subtotal_after_bulk": breakdown.subtotal_after_bulk,
                "coupon_discount": breakdown.coupon_discount,
                "subtotal_after_coupon": breakdown.subtotal_after_coupon,
                "shipping_cost": breakdown.shipping_cost,
                "final_total": breakdown.final_total,
            },
            status=status.HTTP_200_OK,
        )
