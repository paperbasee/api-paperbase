from rest_framework import viewsets

from config.permissions import IsStaffUser
from core.activity import log_activity
from core.models import ActivityLog
from .models import Notification
from .admin_serializers import AdminNotificationSerializer


class AdminNotificationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsStaffUser]
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
