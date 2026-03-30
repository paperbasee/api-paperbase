from rest_framework import viewsets, mixins
from django.db.models import Q

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.admin_dashboard_cache import invalidate_notifications_and_dashboard_caches
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store

from .models import SupportTicket
from .admin_serializers import AdminSupportTicketSerializer


class AdminSupportTicketViewSet(
    StoreRolePermissionMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = AdminSupportTicketSerializer
    queryset = SupportTicket.objects.prefetch_related("attachments").all()
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        qs = qs.filter(store=ctx.store)

        status_value = (self.request.query_params.get("status") or "").strip().lower()
        if status_value == "open":
            status_value = "new"
        if status_value in {"new", "in_progress", "resolved", "closed"}:
            qs = qs.filter(status=status_value)

        priority_value = (self.request.query_params.get("priority") or "").strip().lower()
        if priority_value in {"low", "medium", "high", "urgent"}:
            qs = qs.filter(priority=priority_value)

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(subject__icontains=search)
                | Q(public_id__icontains=search)
                | Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
            )

        return qs

    def perform_destroy(self, instance):
        public_id = instance.public_id
        subject = getattr(instance, "subject", "")
        store_public_id = instance.store.public_id
        super().perform_destroy(instance)
        invalidate_notifications_and_dashboard_caches(store_public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="support_ticket",
            entity_id=public_id,
            summary=f"Support ticket deleted: {subject}" if subject else "Support ticket deleted",
        )
