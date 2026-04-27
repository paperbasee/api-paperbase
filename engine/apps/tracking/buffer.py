"""
Redis Stream helpers for server-side marketing event buffering (Meta CAPI, TikTok Events API).

Meta:
  Stream: capi:meta:stream:{store_public_id}
  Active: capi:meta:active_stores
  Group:  capi-meta-workers

TikTok:
  Stream: capi:tiktok:stream:{store_public_id}
  Active: capi:tiktok:active_stores
  Group:  capi-tiktok-workers

This module is the ONLY place that knows about stream key naming.
"""
from __future__ import annotations

import decimal
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import redis as redis_lib

logger = logging.getLogger(__name__)
_redis_client: redis_lib.Redis | None = None

# --- Stream / set naming (platform-specific; no shared Redis keys between pipelines) ---

_MAX_STREAM_LEN = int(os.environ.get("CAPI_MAX_STREAM_LEN", "5000"))
_META_EARLY_FLUSH_THRESHOLD = int(
    os.environ.get("META_EARLY_FLUSH_THRESHOLD", os.environ.get("CAPI_EARLY_FLUSH_THRESHOLD", "500"))
)
_TIKTOK_EARLY_FLUSH_THRESHOLD = int(os.environ.get("TIKTOK_EARLY_FLUSH_THRESHOLD", "500"))
ACTIVE_STORE_TTL = 3600  # 1 hour

# Public aliases for settings / tests
MAX_STREAM_LEN = _MAX_STREAM_LEN
META_EARLY_FLUSH_THRESHOLD = _META_EARLY_FLUSH_THRESHOLD
TIKTOK_EARLY_FLUSH_THRESHOLD = _TIKTOK_EARLY_FLUSH_THRESHOLD

def _schedule_meta_early_flush(store_public_id: str) -> None:
    from engine.apps.tracking.flush_tasks import flush_store_capi

    flush_store_capi.apply_async(args=[store_public_id], queue="capi", ignore_result=True)


def _schedule_tiktok_early_flush(store_public_id: str) -> None:
    from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

    flush_store_tiktok.apply_async(args=[store_public_id], queue="capi", ignore_result=True)


@dataclass(frozen=True)
class _BufferPlatformSpec:
    stream_key_prefix: str
    active_stores_key: str
    consumer_group: str
    early_flush_threshold: int
    schedule_early_flush: Callable[[str], None]


# Registry keys and string literals for Redis names live only in this block.
_META = "meta"
_TIKTOK = "tiktok"

_BUFFER_PLATFORM_REGISTRY: dict[str, _BufferPlatformSpec] = {
    _META: _BufferPlatformSpec(
        stream_key_prefix="capi:meta:stream:",
        active_stores_key="capi:meta:active_stores",
        consumer_group="capi-meta-workers",
        early_flush_threshold=_META_EARLY_FLUSH_THRESHOLD,
        schedule_early_flush=_schedule_meta_early_flush,
    ),
    _TIKTOK: _BufferPlatformSpec(
        stream_key_prefix="capi:tiktok:stream:",
        active_stores_key="capi:tiktok:active_stores",
        consumer_group="capi-tiktok-workers",
        early_flush_threshold=_TIKTOK_EARLY_FLUSH_THRESHOLD,
        schedule_early_flush=_schedule_tiktok_early_flush,
    ),
}


def _spec(platform: str) -> _BufferPlatformSpec:
    try:
        return _BUFFER_PLATFORM_REGISTRY[platform]
    except KeyError as exc:
        raise ValueError(f"unknown buffer platform: {platform!r}") from exc


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(os.environ["REDIS_URL"])
    return _redis_client


def _stream_key(store_public_id: str, platform: str) -> str:
    return f"{_spec(platform).stream_key_prefix}{store_public_id}"


def _json_dumps(obj: Any) -> str:
    """json.dumps that handles Decimal values from Django/DRF."""

    def default(o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")

    return json.dumps(obj, default=default)


def push_event_to_buffer(
    store_public_id: str,
    payload: dict[str, Any],
    *,
    platform: str,
) -> bool:
    """
    Push a single validated broker payload into the Redis Stream for this store and platform.

    Returns True if pushed, False if Redis is unavailable (caller should log).
    """
    try:
        cfg = _spec(platform)
        r = _get_redis()
        key = _stream_key(store_public_id, platform)

        r.xadd(key, {"payload": _json_dumps(payload)}, maxlen=_MAX_STREAM_LEN, approximate=True)
        r.sadd(cfg.active_stores_key, store_public_id)
        r.expire(cfg.active_stores_key, ACTIVE_STORE_TTL)

        try:
            r.xgroup_create(key, cfg.consumer_group, id="0", mkstream=True)
        except Exception:
            pass

        depth = r.xlen(key)
        if depth >= cfg.early_flush_threshold:
            cfg.schedule_early_flush(store_public_id)

        return True
    except Exception:
        logger.exception(
            "tracking.buffer_push_failed",
            extra={"store_public_id": store_public_id, "platform": platform},
        )
        return False


def read_pending_events(
    r,
    store_public_id: str,
    consumer_name: str,
    count: int = 500,
    *,
    platform: str,
) -> list[tuple[bytes, dict]]:
    cfg = _spec(platform)
    key = _stream_key(store_public_id, platform)
    try:
        result = r.xreadgroup(
            groupname=cfg.consumer_group,
            consumername=consumer_name,
            streams={key: ">"},
            count=count,
            block=None,
        )
    except Exception:
        logger.exception(
            "tracking.buffer_read_failed",
            extra={"store_public_id": store_public_id, "platform": platform},
        )
        return []

    if not result:
        return []

    entries = []
    for _stream_name, messages in result:
        for msg_id, fields in messages:
            entries.append((msg_id, fields))
    return entries


def ack_events(r, store_public_id: str, message_ids: list, *, platform: str) -> None:
    if not message_ids:
        return
    cfg = _spec(platform)
    key = _stream_key(store_public_id, platform)
    try:
        r.xack(key, cfg.consumer_group, *message_ids)
        r.xtrim(key, maxlen=_MAX_STREAM_LEN, approximate=True)
    except Exception:
        logger.exception(
            "tracking.buffer_ack_failed",
            extra={"store_public_id": store_public_id, "count": len(message_ids), "platform": platform},
        )


def get_active_stores(r, *, platform: str) -> list[str]:
    cfg = _spec(platform)
    try:
        members = r.smembers(cfg.active_stores_key)
        return [m.decode() if isinstance(m, bytes) else m for m in members]
    except Exception:
        logger.exception(
            "tracking.buffer_get_active_stores_failed",
            extra={"platform": platform},
        )
        return []


def remove_store_from_active(r, store_public_id: str, *, platform: str) -> None:
    cfg = _spec(platform)
    try:
        r.srem(cfg.active_stores_key, store_public_id)
    except Exception:
        pass
