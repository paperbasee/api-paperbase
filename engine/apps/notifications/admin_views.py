from rest_framework import viewsets, mixins

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.models import ActivityLog
from .models import Notification, SystemNotification
from .admin_serializers import AdminNotificationSerializer, AdminSystemNotificationSerializer


class AdminSystemNotificationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """List/retrieve/update (mark read) system notifications for admin dashboard."""
    permission_classes = [IsDashboardUser]
    serializer_class = AdminSystemNotificationSerializer
    queryset = SystemNotification.objects.all().order_by('-created_at')


class AdminNotificationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsDashboardUser]
    serializer_class = AdminNotificationSerializer
    queryset = Notification.objects.all()

    def perform_create(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="notification",
            entity_id=instance.pk,
            summary="Notification created",
            metadata={"text": getattr(instance, "text", "")},
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="notification",
            entity_id=instance.pk,
            summary="Notification updated",
            metadata={"text": getattr(instance, "text", "")},
        )

    def perform_destroy(self, instance):
        pk = instance.pk
        text = getattr(instance, "text", "")
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="notification",
            entity_id=pk,
            summary="Notification deleted",
            metadata={"text": text},
        )
