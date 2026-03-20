from rest_framework import viewsets

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store

from .models import Review
from .admin_serializers import AdminReviewSerializer


class AdminReviewViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    serializer_class = AdminReviewSerializer
    queryset = Review.objects.select_related("product", "user").order_by("-created_at")
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        return qs.filter(product__store=ctx.store)

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="review",
            entity_id=instance.public_id,
            summary=f"Review updated: {instance.product.name} - {instance.rating} stars",
        )

    def perform_destroy(self, instance):
        public_id = instance.public_id
        product_name = instance.product.name
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="review",
            entity_id=public_id,
            summary=f"Review deleted: {product_name}",
        )
