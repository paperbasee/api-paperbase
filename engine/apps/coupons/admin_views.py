from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store
from engine.apps.products.models import Product
from engine.apps.orders.pricing import PricingEngine
from engine.apps.shipping.models import ShippingMethod, ShippingZone

from .models import BulkDiscount, Coupon
from .admin_serializers import AdminBulkDiscountSerializer, AdminCouponSerializer
from .services import validate_coupon_for_subtotal


class AdminCouponViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    serializer_class = AdminCouponSerializer
    queryset = Coupon.objects.all()
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        return qs.filter(store=ctx.store)

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValueError("No active store for coupon creation")
        instance = serializer.save(store=store)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="coupon",
            entity_id=instance.public_id,
            summary=f"Coupon created: {instance.code}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="coupon",
            entity_id=instance.public_id,
            summary=f"Coupon updated: {instance.code}",
        )

    def perform_destroy(self, instance):
        code = instance.code
        public_id = instance.public_id
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="coupon",
            entity_id=public_id,
            summary=f"Coupon deleted: {code}",
        )

    @action(detail=False, methods=["post"], url_path="apply")
    def apply_coupon(self, request):
        ctx = get_active_store(request)
        if not ctx.store:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        code = (request.data.get("code") or "").strip()
        try:
            subtotal = serializers.DecimalField(max_digits=12, decimal_places=2).to_internal_value(
                request.data.get("subtotal")
            )
        except Exception:
            return Response({"subtotal": "Invalid subtotal."}, status=status.HTTP_400_BAD_REQUEST)

        quote = validate_coupon_for_subtotal(
            store=ctx.store,
            code=code,
            subtotal=subtotal,
            user=request.user if request.user.is_authenticated else None,
        )
        return Response(
            {
                "coupon_public_id": quote.coupon.public_id,
                "code": quote.coupon.code,
                "discount_type": quote.coupon.discount_type,
                "discount_value": quote.coupon.discount_value,
                "discount_amount": quote.discount_amount,
                "subtotal": subtotal,
                "subtotal_after_discount": subtotal - quote.discount_amount,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="pricing-preview")
    def pricing_preview(self, request):
        ctx = get_active_store(request)
        if not ctx.store:
            return Response({"detail": "No active store."}, status=status.HTTP_403_FORBIDDEN)
        items = request.data.get("items") or []
        if not isinstance(items, list) or not items:
            return Response({"items": "At least one item is required."}, status=status.HTTP_400_BAD_REQUEST)
        product_public_ids = [str(item.get("product_public_id", "")).strip() for item in items]
        products = {
            p.public_id: p
            for p in Product.objects.filter(
                store=ctx.store,
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
            pricing_lines.append(
                {
                    "product": product,
                    "quantity": quantity,
                    "unit_price": product.price,
                }
            )
        shipping_zone_public_id = (request.data.get("shipping_zone_public_id") or "").strip()
        shipping_method_public_id = (request.data.get("shipping_method_public_id") or "").strip()
        zone = ShippingZone.objects.filter(
            store=ctx.store,
            public_id=shipping_zone_public_id,
            is_active=True,
        ).first()
        method = None
        if shipping_method_public_id:
            method = ShippingMethod.objects.filter(
                store=ctx.store,
                public_id=shipping_method_public_id,
                is_active=True,
            ).first()

        breakdown = PricingEngine.compute(
            store=ctx.store,
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
                "lines": [
                    {
                        "product_public_id": row.product_id,
                        "quantity": row.quantity,
                        "unit_price": row.unit_price,
                        "line_subtotal": row.line_subtotal,
                        "bulk_rule_public_id": row.bulk_rule_public_id,
                        "bulk_discount_amount": row.bulk_discount_amount,
                    }
                    for row in breakdown.lines
                ],
            },
            status=status.HTTP_200_OK,
        )


class AdminBulkDiscountViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    serializer_class = AdminBulkDiscountSerializer
    queryset = BulkDiscount.objects.all()
    lookup_field = "public_id"

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        return qs.filter(store=ctx.store).select_related("category", "product")

    def get_serializer_context(self):
        context = super().get_serializer_context()
        ctx = get_active_store(self.request)
        context["store"] = ctx.store
        return context

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        if not ctx.store:
            raise serializers.ValidationError({"detail": "No active store."})
        instance = serializer.save(store=ctx.store)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="bulk_discount",
            entity_id=instance.public_id,
            summary=f"Bulk discount created: {instance.public_id}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="bulk_discount",
            entity_id=instance.public_id,
            summary=f"Bulk discount updated: {instance.public_id}",
        )

    def perform_destroy(self, instance):
        public_id = instance.public_id
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="bulk_discount",
            entity_id=public_id,
            summary=f"Bulk discount deleted: {public_id}",
        )
