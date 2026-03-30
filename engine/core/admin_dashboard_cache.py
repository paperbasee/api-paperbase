"""Redis cache keys and invalidation for admin dashboard stats overview."""

from __future__ import annotations

from django.core.cache import cache

from engine.core.admin_notifications_cache import invalidate_notifications_summary_cache


def dashboard_live_overview_cache_key(store_public_id: str, bucket: str) -> str:
    b = (bucket or "day").lower()
    return f"v1:dashboard_live:{store_public_id}:{b}"


def dashboard_stats_cache_key(
    store_public_id: str, start_iso: str, end_iso: str, bucket: str
) -> str:
    b = (bucket or "day").lower()
    return (
        f"v1:dashboard_stats:{store_public_id}:"
        f"{start_iso}:{end_iso}:{b}"
    )


def invalidate_dashboard_live_cache(store_public_id: str) -> None:
    cache.delete_many(
        [
            dashboard_live_overview_cache_key(store_public_id, "day"),
            dashboard_live_overview_cache_key(store_public_id, "week"),
            dashboard_live_overview_cache_key(store_public_id, "month"),
        ]
    )


def invalidate_notifications_and_dashboard_caches(store_public_id: str) -> None:
    """Invalidate notification summary + default dashboard overview for a store."""
    invalidate_notifications_summary_cache(store_public_id)
    invalidate_dashboard_live_cache(store_public_id)
