from datetime import date

from rest_framework import mixins, viewsets

from config.permissions import IsDashboardUser
from .models import ActivityLog
from .admin_serializers import AdminActivityLogSerializer


class AdminActivityLogViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsDashboardUser]
    serializer_class = AdminActivityLogSerializer
    queryset = ActivityLog.objects.select_related("actor").all()

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params

        entity_type = (params.get("entity_type") or "").strip()
        if entity_type:
            qs = qs.filter(entity_type=entity_type)

        action = (params.get("action") or "").strip()
        if action:
            qs = qs.filter(action=action)

        actor = (params.get("actor") or "").strip()
        if actor:
            qs = qs.filter(actor_id=actor)

        q = (params.get("q") or "").strip()
        if q:
            qs = qs.filter(summary__icontains=q)

        start_date = (params.get("start_date") or "").strip()
        if start_date:
            try:
                start = date.fromisoformat(start_date)
                qs = qs.filter(created_at__date__gte=start)
            except ValueError:
                pass

        end_date = (params.get("end_date") or "").strip()
        if end_date:
            try:
                end = date.fromisoformat(end_date)
                qs = qs.filter(created_at__date__lte=end)
            except ValueError:
                pass

        return qs

