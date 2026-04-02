from __future__ import annotations

import logging
from collections.abc import Iterable

from django.conf import settings
from django.core.files.storage import default_storage

logger = logging.getLogger(__name__)


def normalize_media_keys(keys: Iterable[str] | None) -> list[str]:
    cleaned: list[str] = []
    for key in keys or []:
        k = (key or "").strip()
        if not k:
            continue
        cleaned.append(k)
    # Keep original order while removing duplicates.
    return list(dict.fromkeys(cleaned))


def collect_media_keys(instance) -> list[str]:
    getter = getattr(instance, "get_media_keys", None)
    if getter is None:
        return []
    try:
        keys = getter()
    except Exception:
        return []
    return normalize_media_keys(keys)


def schedule_media_deletion(instance) -> int:
    return schedule_media_deletion_from_keys(collect_media_keys(instance))


def schedule_media_deletion_from_keys(keys: Iterable[str] | None) -> int:
    normalized = normalize_media_keys(keys)
    if not normalized:
        return 0
    return delete_media_files(normalized)


def delete_media_files(keys: list[str]) -> int:
    normalized = normalize_media_keys(keys)
    if not normalized:
        return 0

    if getattr(settings, "IS_DEVELOPMENT", False):
        deleted_count = 0
        for key in normalized:
            try:
                if default_storage.exists(key):
                    default_storage.delete(key)
                    deleted_count += 1
                    logger.info("[DEV DELETE] key=%s", key)
            except Exception as exc:
                logger.error("[DEV DELETE FAILED] %s -> %s", key, exc)
        return deleted_count

    from engine.core.tasks import delete_r2_objects

    delete_r2_objects.delay(normalized)
    logger.info("[PROD DELETE DISPATCHED] count=%s", len(normalized))
    return len(normalized)
