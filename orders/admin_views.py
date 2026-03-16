from rest_framework import viewsets, mixins
from rest_framework.decorators import action
from rest_framework.response import Response

from config.permissions import IsStaffUser
from core.activity import log_activity
from core.models import ActivityLog
from .models import Order
from .admin_serializers import (
    AdminOrderListSerializer,
    AdminOrderSerializer,
    AdminOrderStatusSerializer,
)


class AdminOrderViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsStaffUser]
    queryset = Order.objects.prefetch_related('items__product').all()
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.action == 'list':
            return AdminOrderListSerializer
        return AdminOrderSerializer

    @action(detail=True, methods=['patch'], url_path='status')
    def update_status(self, request, pk=None):
        order = self.get_object()
        serializer = AdminOrderStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        prev_status = order.status
        order.status = serializer.validated_data['status']
        order.save(update_fields=['status'])
        if prev_status != order.status:
            log_activity(
                request=request,
                action=ActivityLog.Action.CUSTOM,
                entity_type="order",
                entity_id=order.pk,
                summary=f"Order {order.order_number} status changed: {prev_status} → {order.status}",
                metadata={"from": prev_status, "to": order.status},
            )
        return Response(AdminOrderSerializer(order).data)

    @action(detail=True, methods=['patch'], url_path='tracking')
    def update_tracking(self, request, pk=None):
        order = self.get_object()
        prev_tracking = order.tracking_number
        tracking = request.data.get('tracking_number', '')
        order.tracking_number = tracking
        order.save(update_fields=['tracking_number'])
        if (prev_tracking or "") != (tracking or ""):
            log_activity(
                request=request,
                action=ActivityLog.Action.CUSTOM,
                entity_type="order",
                entity_id=order.pk,
                summary=f"Order {order.order_number} tracking updated",
                metadata={"from": prev_tracking or "", "to": tracking or ""},
            )
        return Response(AdminOrderSerializer(order).data)

    def perform_destroy(self, instance):
        pk = instance.pk
        order_number = getattr(instance, "order_number", "")
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="order",
            entity_id=pk,
            summary=f"Order deleted: {order_number}" if order_number else "Order deleted",
        )
