"""Redis cache keys and invalidation for admin dashboard stats overview."""

from __future__ import annotations

from django.core.cache import cache

from engine.core.admin_notifications_cache import invalidate_notifications_summary_cache


def dashboard_live_overview_cache_key(store_public_id: str, bucket: str) -> str:
    b = (bucket or "day").lower()
    return f"v1:dashboard_live:{store_public_id}:{b}"


def _dashboard_stats_version_key(store_public_id: str) -> str:
    return f"v1:dashboard_stats_version:{store_public_id}"


def get_dashboard_stats_cache_version(store_public_id: str) -> int:
    v = cache.get(_dashboard_stats_version_key(store_public_id))
    try:
        v_int = int(v)
    except (TypeError, ValueError):
        v_int = 1
    return max(v_int, 1)


def bump_dashboard_stats_cache_version(store_public_id: str) -> int:
    """
    Invalidate all dashboard_stats cache entries for a store by bumping a version
    that's embedded in the cache key.
    """
    key = _dashboard_stats_version_key(store_public_id)
    try:
        v = cache.incr(key)
    except Exception:
        # Cache backends may not support incr if key is missing; fall back to set.
        v = (get_dashboard_stats_cache_version(store_public_id) or 1) + 1
        cache.set(key, v, None)
    return int(v)


def dashboard_stats_cache_key(
    store_public_id: str, start_iso: str, end_iso: str, bucket: str
) -> str:
    b = (bucket or "day").lower()
    v = get_dashboard_stats_cache_version(store_public_id)
    return (
        f"v1:dashboard_stats:{store_public_id}:v{v}:"
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
    """Invalidate notification summary + all dashboard overview caches for a store."""
    invalidate_notifications_summary_cache(store_public_id)
    invalidate_dashboard_live_cache(store_public_id)
    bump_dashboard_stats_cache_version(store_public_id)
