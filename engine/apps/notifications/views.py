from rest_framework.generics import ListAPIView
from rest_framework.response import Response

from engine.core.tenancy import require_api_key_store, require_resolved_store

from .serializers import NotificationSerializer
from . import services


class _StorefrontTenantMixin:
    """Public storefront: require API-key resolved tenant before listing."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_resolved_store(request)


class ActiveNotificationListView(_StorefrontTenantMixin, ListAPIView):
    """List currently active notifications for the resolved store (banner display)."""
    serializer_class = NotificationSerializer
    permission_classes = []  # Public endpoint
    authentication_classes = []

    def list(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        data = services.get_active_notifications(store, request)
        return Response(data)
