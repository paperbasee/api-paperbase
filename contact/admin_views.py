from rest_framework import viewsets, mixins

from config.permissions import IsStaffUser
from core.activity import log_activity
from core.models import ActivityLog
from .models import ContactSubmission
from .admin_serializers import AdminContactSubmissionSerializer


class AdminContactSubmissionViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsStaffUser]
    serializer_class = AdminContactSubmissionSerializer
    queryset = ContactSubmission.objects.all()

    def perform_destroy(self, instance):
        pk = instance.pk
        name = getattr(instance, "name", "")
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="contact",
            entity_id=pk,
            summary=f"Contact deleted: {name}" if name else "Contact deleted",
        )
