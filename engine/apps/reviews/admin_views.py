from rest_framework import viewsets

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.models import ActivityLog

from .models import Review
from .admin_serializers import AdminReviewSerializer


class AdminReviewViewSet(viewsets.ModelViewSet):
    permission_classes = [IsDashboardUser]
    serializer_class = AdminReviewSerializer
    queryset = Review.objects.select_related("product", "user").order_by("-created_at")

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="review",
            entity_id=instance.pk,
            summary=f"Review updated: {instance.product.name} - {instance.rating} stars",
        )

    def perform_destroy(self, instance):
        pk = instance.pk
        product_name = instance.product.name
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="review",
            entity_id=pk,
            summary=f"Review deleted: {product_name}",
        )
