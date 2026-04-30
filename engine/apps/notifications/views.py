from rest_framework.generics import ListAPIView
from rest_framework.response import Response

from config.permissions import IsStorefrontAPIKey
from engine.core.http_cache import storefront_cache_headers
from engine.core.tenancy import require_api_key_store, require_resolved_store

from .serializers import StorefrontNotificationSerializer
from . import services


class _StorefrontTenantMixin:
    """Public storefront: require API-key resolved tenant before listing."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_resolved_store(request)


class ActiveNotificationListView(_StorefrontTenantMixin, ListAPIView):
    """List currently active notifications for the resolved store (banner display)."""
    serializer_class = StorefrontNotificationSerializer
    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def list(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        data = services.get_active_notifications(store, request)
        return Response(data)
    list = storefront_cache_headers(max_age=120)(list)
