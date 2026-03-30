"""Redis cache helpers for admin dashboard notifications summary (per store)."""

from __future__ import annotations

import os

from django.core.cache import cache

NOTIFICATIONS_SUMMARY_CACHE_TTL = int(
    os.getenv("NOTIFICATIONS_SUMMARY_CACHE_TTL", "20")
)


def notifications_summary_cache_key(store_public_id: str) -> str:
    return f"v1:notifications_summary:{store_public_id}"


def invalidate_notifications_summary_cache(store_public_id: str) -> None:
    cache.delete(notifications_summary_cache_key(store_public_id))
