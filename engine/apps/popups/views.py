from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsStorefrontAPIKey
from engine.apps.products.views import StorefrontTenantMixin
from django.conf import settings
from engine.core import cache_service
from engine.core.tenancy import require_api_key_store

from . import popup_service
from .serializers import StorePopupSerializer


class StorePopupView(StorefrontTenantMixin, APIView):
    """
    Storefront read-only popup endpoint (tenant-scoped via storefront API key).
    Returns `null` when no active popup exists.
    """

    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    access_scope = "storefront"

    def get(self, request, *args, **kwargs):
        store = require_api_key_store(request)
        cache_key = f"cache:{store.public_id}:popup:active"
        cached = cache_service.get(cache_key)
        if cached is not None:
            return Response(cached)
        popup = popup_service.get_popup(store)

        if popup is None:
            cache_service.set(cache_key, None, settings.CACHE_TTL_STORE_SETTINGS)
            return Response(None)
        if not popup.is_active:
            cache_service.set(cache_key, None, settings.CACHE_TTL_STORE_SETTINGS)
            return Response(None)
        data = StorePopupSerializer(popup, context={"request": request}).data
        cache_service.set(cache_key, data, settings.CACHE_TTL_STORE_SETTINGS)
        return Response(data)

