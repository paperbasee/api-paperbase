from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import F, Prefetch, Q
from django.core.exceptions import ValidationError as DjangoValidationError

from config.permissions import IsDashboardUser
from engine.apps.products.models import ProductVariantAttribute
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.tenant_drf import ProvenTenantContextMixin
from engine.core.query_params import include_inactive_truthy
from engine.core.tenancy import get_active_store
from .models import Inventory, StockMovement
from .admin_serializers import InventoryListSerializer, InventoryDetailSerializer, StockMovementSerializer
from .services import adjust_stock


class AdminInventoryViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    queryset = Inventory.objects.select_related('product', 'variant').order_by('product__name')
    lookup_field = 'public_id'

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return InventoryDetailSerializer
        return InventoryListSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        qs = qs.filter(product__store=ctx.store)

        if self.action == "list" and not include_inactive_truthy(self.request):
            qs = qs.filter(Q(variant__isnull=True) | Q(variant__is_active=True))

        stock_filter = (self.request.query_params.get("stock") or "").strip().lower()
        if stock_filter == "in_stock":
            qs = qs.filter(quantity__gt=0)
        elif stock_filter == "out_of_stock":
            qs = qs.filter(quantity=0)
        elif stock_filter == "low_stock":
            # Match Inventory.is_low_stock(): tracked rows at or below per-row threshold.
            qs = qs.filter(is_tracked=True, quantity__lte=F("low_stock_threshold"))

        tracked_filter = (self.request.query_params.get("tracked") or "").strip().lower()
        if tracked_filter == "tracked":
            qs = qs.filter(is_tracked=True)
        elif tracked_filter == "untracked":
            qs = qs.filter(is_tracked=False)

        record_type = (self.request.query_params.get("type") or "").strip().lower()
        if record_type == "variant":
            qs = qs.filter(variant__isnull=False)
        elif record_type == "product":
            qs = qs.filter(variant__isnull=True)

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(product__name__icontains=search)
                | Q(product__public_id__icontains=search)
                | Q(variant__sku__icontains=search)
                | Q(variant__public_id__icontains=search)
            )

        return qs.prefetch_related(
            Prefetch(
                "variant__attribute_values",
                queryset=ProductVariantAttribute.objects.select_related(
                    "attribute_value__attribute"
                ).order_by("attribute_value__attribute__order", "attribute_value__order"),
            ),
        )

    @action(detail=True, methods=['post'])
    def adjust(self, request, public_id=None):
        """Adjust stock by a delta. Body: { "change": 5, "reason": "restock", "reference": "" }"""
        inventory = self.get_object()
        allowed_fields = {"change", "reason", "reference", "reference_id", "source"}
        unknown_fields = set(request.data.keys()) - allowed_fields
        if unknown_fields:
            return Response(
                {"detail": f"Unknown fields are not allowed: {', '.join(sorted(unknown_fields))}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        change = request.data.get('change')
        if change is None:
            return Response({'detail': 'change is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            change = int(change)
        except (TypeError, ValueError):
            return Response({'detail': 'change must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
        reason = request.data.get('reason', 'adjustment')
        reference = request.data.get('reference', '') or ''
        reference_id = request.data.get("reference_id", "") or ""
        source = request.data.get("source", "admin") or "admin"
        try:
            adjust_stock(
                inventory,
                change,
                reason=reason,
                source=source,
                reference_id=reference_id,
                reference=reference,
                actor=request.user,
            )
        except DjangoValidationError as exc:
            msg = getattr(exc, "message", None) or "; ".join(exc.messages) or "Invalid stock adjustment."
            return Response({"detail": msg}, status=status.HTTP_400_BAD_REQUEST)
        inventory.refresh_from_db()
        return Response(InventoryDetailSerializer(inventory).data)


class AdminStockMovementViewSet(ProvenTenantContextMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsDashboardUser]
    serializer_class = StockMovementSerializer
    lookup_field = "public_id"
    queryset = StockMovement.objects.select_related('inventory__product', 'inventory__variant', 'actor').order_by('-created_at')

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        qs = qs.filter(inventory__product__store=ctx.store)
        # Do NOT accept inventory_id (numeric FK) — use inventory_public_id instead
        inventory_public_id = self.request.query_params.get('inventory_public_id')
        if inventory_public_id:
            qs = qs.filter(inventory__public_id=inventory_public_id)
        return qs
