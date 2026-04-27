"""Minimal broker payload for TikTok Events API Celery tasks (avoid full ingest blobs)."""

from __future__ import annotations

from typing import Any

# PII fields forwarded pass-through for server-side hashing (same set as Meta broker).
PII_FIELDS: tuple[str, ...] = (
    "email",
    "phone",
    "first_name",
    "last_name",
    "external_id",
    "city",
    "state",
    "zip_code",
    "country",
)

EXTRA_FIELDS: tuple[str, ...] = (
    "items",
    "order_id",
)

# Ingest uses Meta standard names; TikTok Events API uses different strings for some.
_META_TO_TIKTOK_EVENT: dict[str, str] = {
    "Purchase": "PlaceAnOrder",
    "PageView": "Pageview",
    "InitiateCheckout": "InitiateCheckout",
    "AddToCart": "AddToCart",
    "ViewContent": "ViewContent",
}


def meta_event_name_to_tiktok(event_name: str) -> str | None:
    """Map validated Meta-style event_name to TikTok Events API ``event`` string."""
    if not event_name:
        return None
    return _META_TO_TIKTOK_EVENT.get(event_name)


def tiktok_enqueue_payload(validated: dict[str, Any], *, client_ip: str | None) -> dict[str, Any]:
    """Subset of validated ingest data required by TikTok flush task."""
    raw_ids = validated.get("content_ids") or []
    if isinstance(raw_ids, list):
        content_ids = [str(x) for x in raw_ids if x is not None and str(x).strip()]
    else:
        content_ids = []

    payload: dict[str, Any] = {
        "event_id": validated["event_id"],
        "event_name": validated["event_name"],
        "event_time": validated["event_time"],
        "event_source_url": validated["event_source_url"],
        "user_agent": validated["user_agent"],
        "client_ip_address": (client_ip or "").strip(),
        "ttp": validated.get("ttp"),
        "ttclid": validated.get("ttclid"),
        "value": validated.get("value", 0.0),
        "currency": validated.get("currency") or "BDT",
        "content_type": validated.get("content_type") or "product",
        "content_ids": content_ids,
    }

    for field in PII_FIELDS:
        value = validated.get(field)
        if value:
            payload[field] = value

    for field in EXTRA_FIELDS:
        value = validated.get(field)
        if value is not None:
            payload[field] = value

    return payload
