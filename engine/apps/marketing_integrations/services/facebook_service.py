"""
Facebook Conversions API integration service.

Sends server-side events to the Meta Marketing API.
Decryption of stored credentials happens exclusively inside this module.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any

import requests

from engine.core.encryption import decrypt_value

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v18.0"
GRAPH_API_BASE = "https://graph.facebook.com"


def _hash_value(value: str) -> str:
    """SHA-256 hash a value for Facebook user_data fields."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _extract_user_data(request) -> dict[str, Any]:
    """Build hashed user_data dict from the incoming request."""
    user_data: dict[str, Any] = {}

    ip = (
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR", "")
    )
    if ip:
        user_data["client_ip_address"] = ip

    ua = request.META.get("HTTP_USER_AGENT", "")
    if ua:
        user_data["client_user_agent"] = ua

    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        email = getattr(user, "email", "") or ""
        if email:
            user_data["em"] = [_hash_value(email)]
        external_id = getattr(user, "public_id", "") or ""
        if external_id:
            user_data["external_id"] = [_hash_value(external_id)]

    return user_data


def _send_event(
    integration,
    event_name: str,
    event_data: dict[str, Any],
    user_data: dict[str, Any],
    *,
    event_id: str | None = None,
) -> None:
    """Post a single event to the Facebook Conversions API."""
    access_token = decrypt_value(integration.access_token_encrypted)
    if not access_token or not integration.pixel_id:
        logger.warning("Facebook integration %s missing credentials, skipping.", integration.public_id)
        return

    url = f"{GRAPH_API_BASE}/{GRAPH_API_VERSION}/{integration.pixel_id}/events"

    event_payload: dict[str, Any] = {
        "event_name": event_name,
        "event_time": int(time.time()),
        "event_id": (event_id or uuid.uuid4().hex),
        "action_source": "website",
        "user_data": user_data,
    }
    if event_data:
        event_payload["custom_data"] = event_data

    body: dict[str, Any] = {
        "data": [event_payload],
        "access_token": access_token,
    }

    test_code = (integration.test_event_code or "").strip()
    if test_code:
        body["test_event_code"] = test_code

    try:
        resp = requests.post(url, json=body, timeout=10)
        resp.raise_for_status()
        logger.info("Facebook event '%s' sent for pixel %s.", event_name, integration.pixel_id)
    except requests.RequestException:
        logger.exception("Failed to send Facebook event '%s' for pixel %s.", event_name, integration.pixel_id)


def track_purchase(request, order, event_id: str | None, integration) -> None:
    user_data = _extract_user_data(request)

    email = getattr(order, "email", "") or ""
    if email:
        user_data["em"] = [_hash_value(email)]

    phone = getattr(order, "phone", "") or ""
    if phone:
        user_data["ph"] = [_hash_value(phone)]

    # Safety: only send Purchase on real conversion success (confirmed order).
    status_value = (getattr(order, "status", "") or "").strip().lower()
    if status_value != "confirmed":
        logger.warning(
            "Skipping Meta Purchase for order %s (status=%s, expected confirmed).",
            getattr(order, "public_id", "—"),
            status_value or "—",
        )
        return

    event_data: dict[str, Any] = {
        "currency": "BDT",
        "value": float(order.total),
        "content_type": "product",
        "order_id": getattr(order, "public_id", "") or "",
    }

    items = list(order.items.select_related("product").all())
    if items:
        event_data["contents"] = [
            {"id": item.product.public_id, "quantity": item.quantity}
            for item in items
            if item.product
        ]
        event_data["num_items"] = sum(i.quantity for i in items if i.product)

    _send_event(integration, "Purchase", event_data, user_data, event_id=event_id)


def track_initiate_checkout(request, event_id: str | None, integration) -> None:
    user_data = _extract_user_data(request)
    _send_event(integration, "InitiateCheckout", {}, user_data, event_id=event_id)


def track_view_content(request, product, event_id: str | None, integration) -> None:
    user_data = _extract_user_data(request)
    event_data = {
        "currency": "BDT",
        "value": float(product.price),
        "content_type": "product",
        "contents": [{"id": product.public_id, "quantity": 1}],
        "content_name": product.name,
    }
    _send_event(integration, "ViewContent", event_data, user_data, event_id=event_id)


def track_search(request, query: str, event_id: str | None, integration) -> None:
    user_data = _extract_user_data(request)
    event_data = {"search_string": query}
    _send_event(integration, "Search", event_data, user_data, event_id=event_id)
