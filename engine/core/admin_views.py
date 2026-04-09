from datetime import date

from rest_framework import mixins, viewsets

from config.permissions import DenyAPIKeyAccess, IsDashboardUser, IsStoreAdmin
from engine.utils.bd_query import apply_bd_date_filters
from engine.core.tenancy import get_active_store
from engine.core.tenant_drf import ProvenTenantContextMixin
from .models import ActivityLog
from .admin_serializers import AdminActivityLogSerializer


class StoreRolePermissionMixin(ProvenTenantContextMixin):
    """
    Mixin that applies role-based permissions to ViewSet actions.

    - Safe read actions (list, retrieve) → IsDashboardUser (any store staff)
    - Write/destructive actions (create, update, partial_update, destroy,
      and any custom action) → IsStoreAdmin (owner or admin only)

    Subclasses must NOT set `permission_classes` directly; they should call
    `super().get_permissions()` via this mixin instead.
    """

    READ_ACTIONS = {"list", "retrieve", "metadata"}

    def get_permissions(self):
        if self.action in self.READ_ACTIONS:
            return [DenyAPIKeyAccess(), IsDashboardUser()]
        return [DenyAPIKeyAccess(), IsStoreAdmin()]


class AdminActivityLogViewSet(
    ProvenTenantContextMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [DenyAPIKeyAccess, IsDashboardUser]
    serializer_class = AdminActivityLogSerializer
    lookup_field = "public_id"
    queryset = ActivityLog.objects.select_related("actor").all()

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        qs = qs.filter(store=ctx.store)

        params = self.request.query_params

        entity_type = (params.get("entity_type") or "").strip()
        if entity_type:
            qs = qs.filter(entity_type=entity_type)

        action = (params.get("action") or "").strip()
        if action:
            qs = qs.filter(action=action)

        actor = (params.get("actor") or "").strip()
        if actor:
            qs = qs.filter(actor__public_id=actor)

        q = (params.get("q") or "").strip()
        if q:
            qs = qs.filter(summary__icontains=q)

        start_raw = (params.get("start_date") or "").strip()
        end_raw = (params.get("end_date") or "").strip()
        start = None
        end = None
        if start_raw:
            try:
                start = date.fromisoformat(start_raw)
            except ValueError:
                pass
        if end_raw:
            try:
                end = date.fromisoformat(end_raw)
            except ValueError:
                pass
        if start is not None or end is not None:
            qs = apply_bd_date_filters(qs, "created_at", start=start, end=end)

        return qs

