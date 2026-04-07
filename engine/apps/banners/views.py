from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from config.permissions import IsStorefrontAPIKey
from engine.apps.products.views import StorefrontTenantMixin
from engine.core.tenancy import get_active_store

from .serializers import PublicBannerSerializer
from . import services
from .models import Banner


class PublicBannerListView(StorefrontTenantMixin, ListAPIView):
    """
    Public list of active banners for the tenant resolved from Host (or X-Store-ID / JWT).
    """

    permission_classes = [IsStorefrontAPIKey]
    authentication_classes = []
    allow_api_key = True
    serializer_class = PublicBannerSerializer
    pagination_class = None
    access_scope = "storefront"

    def list(self, request, *args, **kwargs):
        ctx = get_active_store(request)
        slot = (request.query_params.get("slot") or "").strip()
        if slot:
            allowed = {k for k, _ in Banner.PLACEMENT_CHOICES}
            if slot not in allowed:
                raise ValidationError({"slot": "Invalid placement slot selected"})
        data = services.get_active_banners(ctx.store, request, slot=slot or None)
        return Response(data)
