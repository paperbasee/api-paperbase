"""
TikTok Events API batch flush tasks (isolated from Meta CAPI).

  - coordinate_tiktok_flush: Beat coordinator, dispatches per-store flush tasks
  - flush_store_tiktok: reads TikTok stream, sends batch to TikTok, acks
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
from engine.apps.tracking.tiktok_payload import meta_event_name_to_tiktok
from engine.core.encryption import decrypt_value

logger = logging.getLogger(__name__)
_redis_client: redis_lib.Redis | None = None

TIKTOK_EVENTS_API_URL = "https://business-api.tiktok.com/open_api/v1.3/event/track/"
BATCH_SIZE = int(os.environ.get("CAPI_BATCH_SIZE", "500"))


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.from_url(os.environ["REDIS_URL"])
    return _redis_client


@app.task(
    name="engine.apps.tracking.coordinate_tiktok_flush",
    queue="capi",
    ignore_result=True,
    soft_time_limit=30,
    time_limit=40,
)
def coordinate_tiktok_flush() -> None:
    from engine.apps.tracking.buffer import get_active_stores

    r = _get_redis()
    store_ids = get_active_stores(r, platform="tiktok")
    if not store_ids:
        return

    logger.info("tracking.tiktok_coordinator_dispatching", extra={"store_count": len(store_ids)})
    for store_public_id in store_ids:
        flush_store_tiktok.apply_async(args=[store_public_id], queue="capi", ignore_result=True)


@app.task(
    bind=True,
    name="engine.apps.tracking.flush_store_tiktok",
    queue="capi",
    ignore_result=True,
    max_retries=3,
    soft_time_limit=55,
    time_limit=65,
    acks_late=True,
)
def flush_store_tiktok(self, store_public_id: str) -> None:
    from engine.apps.tracking.buffer import _json_dumps, ack_events, read_pending_events, remove_store_from_active

    r = _get_redis()
    consumer_name = f"celery-{self.request.id or 'unknown'}"

    entries = read_pending_events(r, store_public_id, consumer_name, count=BATCH_SIZE, platform="tiktok")
    if not entries:
        remove_store_from_active(r, store_public_id, platform="tiktok")
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
            logger.warning(
                "tracking.tiktok_flush_bad_json",
                extra={"store_public_id": store_public_id, "msg_id": msg_id},
            )
            bad_ids.append(msg_id)

    if bad_ids:
        ack_events(r, store_public_id, bad_ids, platform="tiktok")

    if not payloads:
        return

    integration_data = _load_tiktok_integration(store_public_id)
    if integration_data is None:
        ack_events(r, store_public_id, message_ids, platform="tiktok")
        remove_store_from_active(r, store_public_id, platform="tiktok")
        return

    pixel_id, access_token, test_event_code, store = integration_data
    tiktok_events = []
    valid_ids = []

    for msg_id, payload in payloads:
        event = _build_tiktok_event(payload, store_public_id)
        if event is not None:
            tiktok_events.append(event)
            valid_ids.append(msg_id)
        else:
            ack_events(r, store_public_id, [msg_id], platform="tiktok")

    if not tiktok_events:
        return

    body: dict[str, Any] = {
        "pixel_code": pixel_id,
        "event_source": "web",
        "partner_name": "paperbase",
        "data": tiktok_events,
    }
    if test_event_code:
        body["test_event_code"] = test_event_code

    headers = {
        "Content-Type": "application/json",
        "Access-Token": access_token,
    }

    try:
        resp = requests.post(
            TIKTOK_EVENTS_API_URL,
            data=_json_dumps(body).encode("utf-8"),
            headers=headers,
            timeout=10,
        )
    except (requests.Timeout, requests.ConnectionError) as exc:
        logger.exception(
            "tracking.tiktok_flush_network_failure",
            extra={"store_public_id": store_public_id, "event_count": len(tiktok_events)},
        )
        _try_db_log(
            store=store,
            status="failed",
            message="network_failure",
            metadata={"event_count": len(tiktok_events)},
        )
        raise self.retry(exc=exc, countdown=30)

    if resp.status_code >= 400:
        logger.warning(
            "tracking.tiktok_flush_rejected",
            extra={
                "store_public_id": store_public_id,
                "http_status": resp.status_code,
                "event_count": len(tiktok_events),
            },
        )
        _try_db_log(
            store=store,
            status="failed",
            message="tiktok_batch_rejected",
            metadata={"http_status": resp.status_code, "event_count": len(tiktok_events)},
        )
        ack_events(r, store_public_id, valid_ids, platform="tiktok")
        return

    ack_events(r, store_public_id, valid_ids, platform="tiktok")
    resp_body = _safe_json(resp) or {}
    _try_db_log(
        store=store,
        status="success",
        message="",
        metadata={"event_count": len(tiktok_events), "tiktok_response": resp_body},
    )

    logger.info(
        "tracking.tiktok_flush_success",
        extra={"store_public_id": store_public_id, "event_count": len(tiktok_events)},
    )


def _load_tiktok_integration(store_public_id: str):
    from engine.apps.marketing_integrations.models import MarketingIntegration
    from engine.apps.stores.models import Store

    store = Store.objects.filter(public_id=store_public_id).first()
    if not store:
        return None

    integration = (
        MarketingIntegration.objects.filter(
            store=store,
            provider=MarketingIntegration.Provider.TIKTOK,
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


def _build_user_block(payload: dict[str, Any]) -> dict[str, Any]:
    """TikTok ``user`` object: hashed PII where applicable; ttp/ttclid/ip/ua plain."""
    user: dict[str, Any] = {}
    client_ip = payload.get("client_ip_address") or ""

    email = payload.get("email") or ""
    if email and "@" in email:
        user["email"] = _normalize_and_hash(email)

    phone = payload.get("phone") or ""
    if phone:
        normalized_phone = _normalize_phone(phone)
        if len(normalized_phone) >= 7:
            user["phone_number"] = _normalize_and_hash(normalized_phone)

    external_id = payload.get("external_id") or ""
    if external_id:
        user["external_id"] = _normalize_and_hash(str(external_id))

    ttp = payload.get("ttp") or ""
    if ttp:
        user["ttp"] = str(ttp)
    ttclid = payload.get("ttclid") or ""
    if ttclid:
        user["ttclid"] = str(ttclid)

    if client_ip:
        user["ip"] = client_ip

    ua = payload.get("user_agent") or ""
    if ua:
        user["user_agent"] = ua

    return user


def _build_properties(payload: dict[str, Any]) -> dict[str, Any]:
    props: dict[str, Any] = {
        "content_type": "product",
    }

    value = payload.get("value")
    if value is not None:
        try:
            props["value"] = float(value)
        except (TypeError, ValueError):
            props["value"] = 0.0
    else:
        props["value"] = 0.0

    currency = payload.get("currency") or ""
    if currency:
        props["currency"] = str(currency).upper()

    items = payload.get("items") or []
    contents: list[dict[str, Any]] = []
    if items:
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id") or item.get("product_id") or ""
            if not item_id:
                continue
            entry: dict[str, Any] = {"content_id": str(item_id)}
            qty = item.get("quantity")
            if qty is not None:
                try:
                    entry["quantity"] = int(qty)
                except (TypeError, ValueError):
                    entry["quantity"] = 1
            price = item.get("item_price") or item.get("price")
            if price is not None:
                try:
                    entry["price"] = float(price)
                except (TypeError, ValueError):
                    pass
            contents.append(entry)

    if not contents:
        raw_cids = payload.get("content_ids") or []
        if isinstance(raw_cids, list):
            for cid in raw_cids:
                if cid is not None and str(cid).strip():
                    contents.append({"content_id": str(cid).strip(), "quantity": 1})

    if contents:
        props["contents"] = contents

    order_id = payload.get("order_id") or ""
    if order_id:
        props["order_id"] = str(order_id)

    props["url"] = str(payload.get("event_source_url") or "")

    return props


def _build_tiktok_event(payload: dict[str, Any], _store_public_id: str) -> dict[str, Any] | None:
    event_name = payload.get("event_name") or ""
    event_id = payload.get("event_id") or ""
    event_time = payload.get("event_time")

    if not event_name or event_name not in ALLOWED_EVENT_NAMES:
        return None
    tiktok_event = meta_event_name_to_tiktok(event_name)
    if not tiktok_event:
        return None
    if not event_id:
        return None
    if not isinstance(event_time, int) or event_time <= 0:
        return None
    if not (payload.get("user_agent") or ""):
        return None

    user = _build_user_block(payload)
    if not user:
        return None

    return {
        "event": tiktok_event,
        "event_time": event_time,
        "event_id": event_id,
        "user": user,
        "properties": _build_properties(payload),
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
            event_type="tiktok_events_api_batch",
            status=status,
            message=(message or "").strip()[:500],
            metadata=metadata or {},
        )
    except Exception:
        logger.warning("tracking.tiktok_store_event_log_failed", exc_info=True)
