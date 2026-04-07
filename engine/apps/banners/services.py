"""Cache-backed read service for storefront banner data."""

from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from engine.core import cache_service

from .models import Banner
from .serializers import PublicBannerSerializer


def get_active_banners(store, request, slot: str | None = None):
    """Return cached active banners list for the storefront, falling back to DB."""
    key = cache_service.build_key(store.public_id, "banners", "active", slot or "all")

    def fetcher():
        now = timezone.now()
        qs = (
            Banner.objects.filter(store=store, is_active=True)
            .filter(Q(start_at__isnull=True) | Q(start_at__lte=now))
            .filter(Q(end_at__isnull=True) | Q(end_at__gte=now))
            .order_by("order", "id")
        )
        if slot:
            qs = qs.filter(placement_slots__contains=[slot])
        return PublicBannerSerializer(
            qs, many=True, context={"request": request}
        ).data

    return cache_service.get_or_set(key, fetcher, settings.CACHE_TTL_BANNERS)


def invalidate_banner_cache(store_public_id: str) -> None:
    """Clear banner caches for a store."""
    cache_service.invalidate_store_resource(store_public_id, "banners")
