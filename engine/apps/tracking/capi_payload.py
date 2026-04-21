"""Minimal broker payload for Meta CAPI Celery tasks (avoid full ingest blobs)."""

from __future__ import annotations

from typing import Any


def capi_enqueue_payload(validated: dict[str, Any], *, client_ip: str | None) -> dict[str, Any]:
    """Subset of validated ingest data required by ``send_capi_event``."""
    raw_ids = validated.get("content_ids") or []
    if isinstance(raw_ids, list):
        content_ids = [str(x) for x in raw_ids if x is not None and str(x).strip()]
    else:
        content_ids = []
    return {
        "event_id": validated["event_id"],
        "event_name": validated["event_name"],
        "event_time": validated["event_time"],
        "event_source_url": validated["event_source_url"],
        "user_agent": validated["user_agent"],
        "client_ip_address": (client_ip or "").strip(),
        "fbp": validated.get("fbp"),
        "fbc": validated.get("fbc"),
        "value": validated.get("value", 0.0),
        "currency": validated.get("currency") or "BDT",
        "content_type": validated.get("content_type") or "product",
        "content_ids": content_ids,
    }
