from __future__ import annotations

import json
import logging
from typing import Any

from django.core.cache import cache

from .presets import PALETTE_VERSION

logger = logging.getLogger(__name__)

THEME_CACHE_TTL = 60 * 60 * 24  # 24 hours
PRESETS_CACHE_TTL = 60 * 60 * 24
# Bump when palette keys/labels change so stale entries are not served for 24h.
PRESETS_CACHE_KEY = "theme:presets:v2"


def get_cache_key(store_public_id: str) -> str:
    return f"theme:storefront:{store_public_id}:{PALETTE_VERSION}"


def theme_cache_key(store_public_id: str) -> str:
    return get_cache_key(store_public_id)


def get_cached_theme(store_public_id: str) -> dict[str, Any] | None:
    key = theme_cache_key(store_public_id)
    try:
        raw = cache.get(key)
    except Exception:
        logger.warning("theme cache get failed for %s", key, exc_info=True)
        return None
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def set_cached_theme(store_public_id: str, data: dict[str, Any]) -> None:
    key = theme_cache_key(store_public_id)
    try:
        cache.set(key, json.dumps(data, default=str), THEME_CACHE_TTL)
    except Exception:
        logger.warning("theme cache set failed for %s", key, exc_info=True)


def invalidate_theme_cache(store_public_id: str) -> None:
    key = theme_cache_key(store_public_id)
    try:
        cache.delete(key)
    except Exception:
        logger.warning("theme cache delete failed for %s", key, exc_info=True)
