from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store
from .models import Order
from .admin_serializers import (
    AdminOrderListSerializer,
    AdminOrderSerializer,
    AdminOrderCreateSerializer,
    AdminOrderUpdateSerializer,
    AdminOrderStatusSerializer,
)


class AdminOrderViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsDashboardUser]
    queryset = Order.objects.prefetch_related('items__product').all()
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.action == "create":
            return AdminOrderCreateSerializer
        if self.action == 'list':
            return AdminOrderListSerializer
        if self.action in ("update", "partial_update"):
            return AdminOrderUpdateSerializer
        return AdminOrderSerializer

    def update(self, request, *args, **kwargs):
        """
        Use AdminOrderUpdateSerializer for validation/write, but always respond with AdminOrderSerializer.

        This avoids response serialization errors (items are write-only in update serializer).
        """
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = AdminOrderUpdateSerializer(
            instance,
            data=request.data,
            partial=partial,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance.refresh_from_db()
        return Response(AdminOrderSerializer(instance).data, status=status.HTTP_200_OK)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if ctx.store:
            return qs.filter(store=ctx.store)
        return qs

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValidationError(
                {
                    "detail": (
                        "No active store resolved. Re-login, switch store, or send the "
                        "X-Store-ID header."
                    )
                }
            )
        instance = serializer.save(store=store)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="order",
            entity_id=instance.pk,
            summary=f"Order created: {instance.order_number}",
        )

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
