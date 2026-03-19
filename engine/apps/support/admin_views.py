from rest_framework import viewsets, mixins

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store

from .models import SupportTicket
from .admin_serializers import AdminSupportTicketSerializer


class AdminSupportTicketViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsDashboardUser]
    serializer_class = AdminSupportTicketSerializer
    queryset = SupportTicket.objects.prefetch_related("attachments").all()
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if ctx.store:
            return qs.filter(store=ctx.store)
        return qs

    def perform_destroy(self, instance):
        pk = instance.pk
        subject = getattr(instance, "subject", "")
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="support_ticket",
            entity_id=pk,
            summary=f"Support ticket deleted: {subject}" if subject else "Support ticket deleted",
        )


# Backwards-compat alias for existing router path `contacts/`
AdminContactSubmissionViewSet = AdminSupportTicketViewSet
