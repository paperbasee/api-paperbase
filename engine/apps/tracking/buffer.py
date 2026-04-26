"""
Redis Stream helpers for CAPI event buffering.

Stream key:         capi:stream:{store_public_id}
Active store set:   capi:active_stores
Consumer group:     capi-workers

This module is the ONLY place that knows about stream key naming.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import redis as redis_lib

logger = logging.getLogger(__name__)
_redis_client: redis_lib.Redis | None = None

# Stream config
STREAM_KEY_PREFIX = "capi:stream:"
ACTIVE_STORES_KEY = "capi:active_stores"
CONSUMER_GROUP = "capi-workers"
MAX_STREAM_LEN = int(os.environ.get("CAPI_MAX_STREAM_LEN", "5000"))
EARLY_FLUSH_THRESHOLD = int(os.environ.get("CAPI_EARLY_FLUSH_THRESHOLD", "500"))
ACTIVE_STORE_TTL = 3600  # 1 hour


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(os.environ["REDIS_URL"])
    return _redis_client


def _stream_key(store_public_id: str) -> str:
    return f"{STREAM_KEY_PREFIX}{store_public_id}"


def push_event_to_buffer(store_public_id: str, payload: dict[str, Any]) -> bool:
    """
    Push a single validated CAPI payload into the Redis Stream for this store.

    Returns True if pushed, False if Redis is unavailable (caller should log).
    """
    try:
        r = _get_redis()
        key = _stream_key(store_public_id)

        r.xadd(key, {"payload": json.dumps(payload)}, maxlen=MAX_STREAM_LEN, approximate=True)
        r.sadd(ACTIVE_STORES_KEY, store_public_id)
        r.expire(ACTIVE_STORES_KEY, ACTIVE_STORE_TTL)

        try:
            r.xgroup_create(key, CONSUMER_GROUP, id="0", mkstream=True)
        except Exception:
            pass

        depth = r.xlen(key)
        if depth >= EARLY_FLUSH_THRESHOLD:
            from engine.apps.tracking.flush_tasks import flush_store_capi

            flush_store_capi.apply_async(
                args=[store_public_id],
                queue="capi",
                ignore_result=True,
            )

        return True
    except Exception:
        logger.exception(
            "tracking.buffer_push_failed",
            extra={"store_public_id": store_public_id},
        )
        return False


def read_pending_events(
    r,
    store_public_id: str,
    consumer_name: str,
    count: int = 500,
) -> list[tuple[bytes, dict]]:
    key = _stream_key(store_public_id)
    try:
        result = r.xreadgroup(
            groupname=CONSUMER_GROUP,
            consumername=consumer_name,
            streams={key: ">"},
            count=count,
            block=None,
        )
    except Exception:
        logger.exception(
            "tracking.buffer_read_failed",
            extra={"store_public_id": store_public_id},
        )
        return []

    if not result:
        return []

    entries = []
    for _stream_name, messages in result:
        for msg_id, fields in messages:
            entries.append((msg_id, fields))
    return entries


def ack_events(r, store_public_id: str, message_ids: list) -> None:
    if not message_ids:
        return
    key = _stream_key(store_public_id)
    try:
        r.xack(key, CONSUMER_GROUP, *message_ids)
        r.xtrim(key, maxlen=MAX_STREAM_LEN, approximate=True)
    except Exception:
        logger.exception(
            "tracking.buffer_ack_failed",
            extra={"store_public_id": store_public_id, "count": len(message_ids)},
        )


def get_active_stores(r) -> list[str]:
    try:
        members = r.smembers(ACTIVE_STORES_KEY)
        return [m.decode() if isinstance(m, bytes) else m for m in members]
    except Exception:
        logger.exception("tracking.buffer_get_active_stores_failed")
        return []


def remove_store_from_active(r, store_public_id: str) -> None:
    try:
        r.srem(ACTIVE_STORES_KEY, store_public_id)
    except Exception:
        pass
