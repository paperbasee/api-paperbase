from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from config.permissions import IsDashboardUser
from .models import Inventory, StockMovement
from .admin_serializers import InventoryListSerializer, InventoryDetailSerializer, StockMovementSerializer
from .services import adjust_stock


class AdminInventoryViewSet(viewsets.ModelViewSet):
    permission_classes = [IsDashboardUser]
    queryset = Inventory.objects.select_related('product', 'variant').order_by('product__name')
    lookup_field = 'public_id'

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return InventoryDetailSerializer
        return InventoryListSerializer

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
        inventory_id = self.request.query_params.get('inventory_id')
        if inventory_id:
            qs = qs.filter(inventory_id=inventory_id)
        return qs
