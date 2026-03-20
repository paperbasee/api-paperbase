from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from config.permissions import IsDashboardUser
from engine.core.admin_views import StoreRolePermissionMixin
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
        return qs.filter(product__store=ctx.store)

    @action(detail=True, methods=['post'])
    def adjust(self, request, pk=None):
        """Adjust stock by a delta. Body: { "change": 5, "reason": "restock", "reference": "" }"""
        inventory = self.get_object()
        change = request.data.get('change')
        if change is None:
            return Response({'detail': 'change is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            change = int(change)
        except (TypeError, ValueError):
            return Response({'detail': 'change must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
        reason = request.data.get('reason', 'adjustment')
        reference = request.data.get('reference', '') or ''
        adjust_stock(inventory, change, reason=reason, reference=reference, actor=request.user)
        inventory.refresh_from_db()
        return Response(InventoryDetailSerializer(inventory).data)


class AdminStockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsDashboardUser]
    serializer_class = StockMovementSerializer
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
