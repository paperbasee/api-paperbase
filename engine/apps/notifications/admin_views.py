from django.db.models import Q
from rest_framework import viewsets, mixins

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.models import ActivityLog
from .models import Notification, StaffInboxNotification
from .admin_serializers import AdminNotificationSerializer, AdminStaffInboxNotificationSerializer


class AdminStaffInboxNotificationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """List/retrieve/update (mark read) staff inbox notifications for admin dashboard."""
    permission_classes = [IsDashboardUser]
    serializer_class = AdminStaffInboxNotificationSerializer
    queryset = StaffInboxNotification.objects.all().order_by('-created_at')
    lookup_field = 'public_id'

    def get_queryset(self):
        # Show notifications targeted at this user OR global ones (user=null).
        return super().get_queryset().filter(
            Q(user=self.request.user) | Q(user__isnull=True)
        )


class AdminNotificationViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    serializer_class = AdminNotificationSerializer
    queryset = Notification.objects.all()
    lookup_field = 'public_id'

    def perform_create(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="notification",
            entity_id=instance.public_id,
            summary="Notification created",
            metadata={"text": getattr(instance, "text", "")},
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="notification",
            entity_id=instance.public_id,
            summary="Notification updated",
            metadata={"text": getattr(instance, "text", "")},
        )

    def perform_destroy(self, instance):
        public_id = instance.public_id
        text = getattr(instance, "text", "")
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="notification",
            entity_id=public_id,
            summary="Notification deleted",
            metadata={"text": text},
        )
