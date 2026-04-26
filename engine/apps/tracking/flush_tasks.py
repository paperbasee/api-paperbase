"""
CAPI batch flush tasks.

Two tasks:
  - coordinate_capi_flush: runs every 10s via Beat, dispatches per-store tasks
  - flush_store_capi: reads stream, validates, sends batch to Meta, acks
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

import redis as redis_lib
import requests

from config.celery import app
from engine.apps.tracking.contract import ALLOWED_EVENT_NAMES
from engine.core.encryption import decrypt_value

logger = logging.getLogger(__name__)
_redis_client: redis_lib.Redis | None = None

GRAPH_API_VERSION = os.environ.get("META_GRAPH_API_VERSION", "v25.0")
GRAPH_API_BASE = "https://graph.facebook.com"
BATCH_SIZE = int(os.environ.get("CAPI_BATCH_SIZE", "500"))


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(os.environ["REDIS_URL"])
    return _redis_client


@app.task(
    name="engine.apps.tracking.coordinate_capi_flush",
    queue="capi",
    ignore_result=True,
    soft_time_limit=30,
    time_limit=40,
)
def coordinate_capi_flush() -> None:
    from engine.apps.tracking.buffer import get_active_stores

    r = _get_redis()
    store_ids = get_active_stores(r)
    if not store_ids:
        return

    logger.info("tracking.coordinator_dispatching", extra={"store_count": len(store_ids)})
    for store_public_id in store_ids:
        flush_store_capi.apply_async(args=[store_public_id], queue="capi", ignore_result=True)


@app.task(
    bind=True,
    name="engine.apps.tracking.flush_store_capi",
    queue="capi",
    ignore_result=True,
    max_retries=3,
    soft_time_limit=55,
    time_limit=65,
    acks_late=True,
)
def flush_store_capi(self, store_public_id: str) -> None:
    from engine.apps.tracking.buffer import ack_events, read_pending_events, remove_store_from_active

    r = _get_redis()
    consumer_name = f"celery-{self.request.id or 'unknown'}"

    entries = read_pending_events(r, store_public_id, consumer_name, count=BATCH_SIZE)
    if not entries:
        remove_store_from_active(r, store_public_id)
        return

    message_ids = [entry[0] for entry in entries]
    payloads = []
    bad_ids = []

    for msg_id, fields in entries:
        raw = fields.get(b"payload") or fields.get("payload") or b""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            payloads.append((msg_id, json.loads(raw)))
        except json.JSONDecodeError:
            logger.warning("tracking.flush_bad_json", extra={"store_public_id": store_public_id, "msg_id": msg_id})
            bad_ids.append(msg_id)

    if bad_ids:
        ack_events(r, store_public_id, bad_ids)

    if not payloads:
        return

    integration_data = _load_integration(store_public_id)
    if integration_data is None:
        ack_events(r, store_public_id, message_ids)
        remove_store_from_active(r, store_public_id)
        return

    pixel_id, access_token, test_event_code, store = integration_data
    meta_events = []
    valid_ids = []

    for msg_id, payload in payloads:
        event = _build_meta_event(payload, store_public_id)
        if event is not None:
            meta_events.append(event)
            valid_ids.append(msg_id)
        else:
            ack_events(r, store_public_id, [msg_id])

    if not meta_events:
        return

    body: dict[str, Any] = {"data": meta_events, "access_token": access_token}
    if test_event_code:
        body["test_event_code"] = test_event_code

    url = f"{GRAPH_API_BASE}/{GRAPH_API_VERSION}/{pixel_id}/events"
    try:
        resp = requests.post(url, json=body, timeout=10)
    except (requests.Timeout, requests.ConnectionError) as exc:
        logger.exception(
            "tracking.flush_network_failure",
            extra={"store_public_id": store_public_id, "event_count": len(meta_events)},
        )
        _try_db_log(
            store=store,
            status="failed",
            message="network_failure",
            metadata={"event_count": len(meta_events)},
        )
        raise self.retry(exc=exc, countdown=30)

    if resp.status_code >= 400:
        logger.warning(
            "tracking.flush_meta_rejected",
            extra={
                "store_public_id": store_public_id,
                "http_status": resp.status_code,
                "event_count": len(meta_events),
            },
        )
        _try_db_log(
            store=store,
            status="failed",
            message="meta_batch_rejected",
            metadata={"http_status": resp.status_code, "event_count": len(meta_events)},
        )
        ack_events(r, store_public_id, valid_ids)
        return

    ack_events(r, store_public_id, valid_ids)
    resp_body = _safe_json(resp) or {}
    _try_db_log(
        store=store,
        status="success",
        message="",
        metadata={"event_count": len(meta_events), "meta_response": resp_body},
    )

    logger.info("tracking.flush_success", extra={"store_public_id": store_public_id, "event_count": len(meta_events)})


def _load_integration(store_public_id: str):
    from engine.apps.marketing_integrations.models import MarketingIntegration
    from engine.apps.stores.models import Store

    store = Store.objects.filter(public_id=store_public_id).first()
    if not store:
        return None

    integration = (
        MarketingIntegration.objects.filter(
            store=store,
            provider=MarketingIntegration.Provider.FACEBOOK,
            is_active=True,
        )
        .only("pixel_id", "access_token_encrypted", "test_event_code", "public_id", "store_id")
        .first()
    )
    if not integration:
        return None

    pixel_id = (getattr(integration, "pixel_id", "") or "").strip()
    access_token = decrypt_value(getattr(integration, "access_token_encrypted", "") or "")
    test_event_code = (getattr(integration, "test_event_code", "") or "").strip()

    if not pixel_id or not access_token:
        return None

    return pixel_id, access_token, test_event_code, store


def _normalize_and_hash(value: str) -> str:
    normalized = value.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_phone(phone: str) -> str:
    return re.sub(r"[^\d]", "", phone)


def _build_user_data(payload: dict[str, Any]) -> dict[str, Any]:
    ud: dict[str, Any] = {}
    client_ip = payload.get("client_ip_address") or ""

    email = payload.get("email") or ""
    if email and "@" in email:
        ud["em"] = [_normalize_and_hash(email)]

    phone = payload.get("phone") or ""
    if phone:
        normalized_phone = _normalize_phone(phone)
        if len(normalized_phone) >= 7:
            ud["ph"] = [_normalize_and_hash(normalized_phone)]

    for field, key in [("first_name", "fn"), ("last_name", "ln")]:
        val = payload.get(field) or ""
        if val:
            ud[key] = [_normalize_and_hash(val)]

    external_id = payload.get("external_id") or ""
    if external_id:
        ud["external_id"] = [_normalize_and_hash(str(external_id))]

    for field, key in [("city", "ct"), ("state", "st"), ("zip_code", "zp")]:
        val = payload.get(field) or ""
        if val:
            ud[key] = [_normalize_and_hash(val)]

    country = payload.get("country") or ""
    if country:
        ud["country"] = [_normalize_and_hash(country.lower())]

    fbp = payload.get("fbp") or ""
    if fbp:
        ud["fbp"] = fbp
    fbc = payload.get("fbc") or ""
    if fbc:
        ud["fbc"] = fbc
    if client_ip:
        ud["client_ip_address"] = client_ip
    ua = payload.get("user_agent") or ""
    if ua:
        ud["client_user_agent"] = ua

    return ud


def _build_meta_event(payload: dict[str, Any], store_public_id: str) -> dict[str, Any] | None:
    event_name = payload.get("event_name") or ""
    event_id = payload.get("event_id") or ""
    event_time = payload.get("event_time")

    if not event_name or event_name not in ALLOWED_EVENT_NAMES:
        return None
    if not event_id:
        return None
    if not isinstance(event_time, int) or event_time <= 0:
        return None
    if not (payload.get("user_agent") or ""):
        return None

    user_data = _build_user_data(payload)
    if not user_data:
        return None

    custom_data: dict[str, Any] = {}
    value = payload.get("value")
    if value is not None:
        try:
            custom_data["value"] = float(value)
        except (TypeError, ValueError):
            pass

    currency = payload.get("currency") or ""
    if currency:
        custom_data["currency"] = currency.upper()

    content_ids = payload.get("content_ids") or []
    if content_ids:
        custom_data["content_ids"] = content_ids
        custom_data["content_type"] = "product"

    items = payload.get("items") or []
    if items:
        contents = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id") or item.get("product_id") or ""
            if not item_id:
                continue
            entry: dict[str, Any] = {"id": str(item_id)}
            qty = item.get("quantity")
            if qty is not None:
                try:
                    entry["quantity"] = int(qty)
                except (TypeError, ValueError):
                    entry["quantity"] = 1
            price = item.get("item_price") or item.get("price")
            if price is not None:
                try:
                    entry["item_price"] = float(price)
                except (TypeError, ValueError):
                    pass
            contents.append(entry)
        if contents:
            custom_data["contents"] = contents
            custom_data["num_items"] = len(contents)
    elif content_ids:
        custom_data["num_items"] = len(content_ids)

    order_id = payload.get("order_id") or ""
    if order_id:
        custom_data["order_id"] = str(order_id)

    return {
        "event_name": event_name,
        "event_time": event_time,
        "event_id": event_id,
        "action_source": "website",
        "event_source_url": str(payload.get("event_source_url") or ""),
        "user_data": user_data,
        "custom_data": custom_data,
    }


def _safe_json(resp: requests.Response) -> dict[str, Any] | None:
    try:
        data = resp.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _try_db_log(*, store, status: str, message: str, metadata: dict[str, Any] | None = None) -> None:
    try:
        from engine.apps.marketing_integrations.models import StoreEventLog

        StoreEventLog.objects.create(
            store=store,
            app="tracking",
            event_type="meta_capi_batch",
            status=status,
            message=(message or "").strip()[:500],
            metadata=metadata or {},
        )
    except Exception:
        logger.warning("tracking.store_event_log_failed", exc_info=True)
