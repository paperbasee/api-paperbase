"""Cache-backed read service for storefront notification / CTA data."""

from __future__ import annotations

from django.conf import settings

from engine.core import cache_service

from .models import StorefrontCTA
from .serializers import StorefrontNotificationSerializer


def get_active_notifications(store, request):
    """Return cached storefront CTAs (is_active only); clients use is_currently_active + start/end for display."""
    key = cache_service.build_key(store.public_id, "notifications", "active")

    def fetcher():
        qs = StorefrontCTA.objects.filter(store=store, is_active=True)
        return StorefrontNotificationSerializer(
            qs, many=True, context={"request": request}
        ).data

    return cache_service.get_or_set(key, fetcher, settings.CACHE_TTL_NOTIFICATIONS)


def invalidate_notification_cache(store_public_id: str) -> None:
    """Clear notification caches for a store."""
    cache_service.invalidate_store_resource(store_public_id, "notifications")
