from django.db.models import Q
from django.utils import timezone
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny

from engine.apps.products.views import StorefrontTenantMixin
from engine.core.tenancy import get_active_store

from .models import Banner
from .serializers import PublicBannerSerializer


class PublicBannerListView(StorefrontTenantMixin, ListAPIView):
    """
    Public list of active banners for the tenant resolved from Host (or X-Store-ID / JWT).
    """

    permission_classes = [AllowAny]
    serializer_class = PublicBannerSerializer
    pagination_class = None

    def get_queryset(self):
        ctx = get_active_store(self.request)
        now = timezone.now()
        return (
            Banner.objects.filter(store=ctx.store, is_active=True)
            .filter(Q(start_at__isnull=True) | Q(start_at__lte=now))
            .filter(Q(end_at__isnull=True) | Q(end_at__gte=now))
            .order_by("order", "id")
        )
