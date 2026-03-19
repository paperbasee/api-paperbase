from rest_framework import viewsets

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store

from .models import Coupon
from .admin_serializers import AdminCouponSerializer


class AdminCouponViewSet(viewsets.ModelViewSet):
    permission_classes = [IsDashboardUser]
    serializer_class = AdminCouponSerializer
    queryset = Coupon.objects.all()
    lookup_field = 'public_id'

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
            raise ValueError("No active store for coupon creation")
        instance = serializer.save(store=store)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="coupon",
            entity_id=instance.pk,
            summary=f"Coupon created: {instance.code}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="coupon",
            entity_id=instance.pk,
            summary=f"Coupon updated: {instance.code}",
        )

    def perform_destroy(self, instance):
        code = instance.code
        pk = instance.pk
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="coupon",
            entity_id=pk,
            summary=f"Coupon deleted: {code}",
        )
