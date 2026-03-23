from django.db import models
from rest_framework.generics import ListAPIView

from engine.core.tenancy import get_active_store, require_resolved_store

from .models import StorefrontCTA
from .serializers import NotificationSerializer


class _StorefrontTenantMixin:
    """Public storefront: require host-resolved (or header) tenant before listing."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_resolved_store(request)


class ActiveNotificationListView(_StorefrontTenantMixin, ListAPIView):
    """List currently active notifications for the resolved store (banner display)."""
    serializer_class = NotificationSerializer
    permission_classes = []  # Public endpoint
    authentication_classes = []

    def get_queryset(self):
        ctx = get_active_store(self.request)
        qs = StorefrontCTA.objects.filter(store=ctx.store, is_active=True)
        from django.utils import timezone

        now = timezone.now()
        qs = qs.filter(
            models.Q(start_date__isnull=True) | models.Q(start_date__lte=now),
            models.Q(end_date__isnull=True) | models.Q(end_date__gte=now),
        )
        return qs
