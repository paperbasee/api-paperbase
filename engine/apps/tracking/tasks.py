from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

import requests
from django.utils import timezone

from config.celery import app
from engine.core.encryption import decrypt_value
from engine.apps.tracking.contract import ALLOWED_EVENT_NAMES

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = os.environ.get("META_GRAPH_API_VERSION", "v25.0")
GRAPH_API_BASE = "https://graph.facebook.com"

EVENT_LOG_RETENTION_HOURS = int(os.environ.get("EVENT_LOG_RETENTION_HOURS", "72"))


# ---------------------------------------------------------------------------
# PII hashing helpers
# ---------------------------------------------------------------------------

def _normalize_and_hash(value: str) -> str:
    """SHA-256 hash a normalized string per Meta's spec."""
    normalized = value.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_phone(phone: str) -> str:
    """Strip all non-digit characters. Meta expects digits only."""
    return re.sub(r"[^\d]", "", phone)


def _build_user_data(payload: dict[str, Any], client_ip: str | None) -> dict[str, Any]:
    """
    Build CAPI user_data dict. All PII fields are SHA-256 hashed per Meta spec
    before being included. Non-PII signals are passed as-is.

    Reference:
    https://developers.facebook.com/docs/marketing-api/conversions-api/parameters/customer-information-parameters
    """
    ud: dict[str, Any] = {}

    # Hashed PII fields
    email = payload.get("email") or ""
    if email and "@" in email:
        ud["em"] = [_normalize_and_hash(email)]

    phone = payload.get("phone") or ""
    if phone:
        normalized_phone = _normalize_phone(phone)
        if len(normalized_phone) >= 7:
            ud["ph"] = [_normalize_and_hash(normalized_phone)]

    first_name = payload.get("first_name") or ""
    if first_name:
        ud["fn"] = [_normalize_and_hash(first_name)]

    last_name = payload.get("last_name") or ""
    if last_name:
        ud["ln"] = [_normalize_and_hash(last_name)]

    external_id = payload.get("external_id") or ""
    if external_id:
        ud["external_id"] = [_normalize_and_hash(str(external_id))]

    city = payload.get("city") or ""
    if city:
        ud["ct"] = [_normalize_and_hash(city)]

    state = payload.get("state") or ""
    if state:
        ud["st"] = [_normalize_and_hash(state)]

    zip_code = payload.get("zip_code") or ""
    if zip_code:
        ud["zp"] = [_normalize_and_hash(zip_code)]

    country = payload.get("country") or ""
    if country:
        ud["country"] = [_normalize_and_hash(country.lower())]

    # Non-hashed browser signals — passed as-is per Meta spec
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


def _try_db_log(*, store, status: str, event_name: str, message: str, metadata: dict[str, Any] | None = None) -> None:
    try:
        from engine.apps.marketing_integrations.models import StoreEventLog

        StoreEventLog.objects.create(
            store=store,
            app="tracking",
            event_type=f"meta_capi_{(event_name or '').strip().lower()}",
            status=status,
            message=(message or "").strip()[:500],
            metadata=metadata or {},
        )
    except Exception:
        logger.warning(
            "tracking.store_event_log_failed",
            exc_info=True,
            extra={"event_name": event_name, "status": status},
        )
        return


def _safe_json(resp: requests.Response) -> dict[str, Any] | None:
    try:
        data = resp.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _classify_meta_http_failure(resp: requests.Response) -> tuple[str, dict[str, Any]]:
    """
    Classify Meta Graph API non-2xx responses.

    Returns (classification, metadata).
    """
    meta: dict[str, Any] = {
        "http_status": resp.status_code,
    }
    body = _safe_json(resp) or {}
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    meta_error = {
        "type": err.get("type"),
        "code": err.get("code"),
        "error_subcode": err.get("error_subcode"),
        "message": err.get("message"),
        "fbtrace_id": err.get("fbtrace_id"),
    }
    meta["meta_error"] = meta_error

    code = err.get("code")
    etype = (err.get("type") or "").lower()
    msg = (err.get("message") or "").lower()

    if code == 190 or "oauth" in etype or "access token" in msg:
        return "invalid_token", meta
    if resp.status_code == 404 or "unsupported get request" in msg or "object with id" in msg:
        return "invalid_pixel_id", meta
    return "meta_api_rejection", meta


@app.task(
    bind=True,
    # Retry only for network/timeouts. Do not retry deterministic Meta rejections.
    autoretry_for=(requests.Timeout, requests.ConnectionError),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
    acks_late=True,
    soft_time_limit=25,
    time_limit=35,
    name="engine.apps.tracking.send_capi_event",
)
def send_capi_event(
    self,
    store_public_id: str,
    event_name: str,
    event_id: str,
    payload: dict[str, Any],
) -> None:
    """
    CAPI engine worker.

    Inputs come from tracker.js via Django ingestion; event_id is never generated
    server-side.
    """

    from engine.apps.marketing_integrations.models import IntegrationEventSettings, MarketingIntegration
    from engine.apps.stores.models import Store

    spid = (store_public_id or "").strip()
    if not spid:
        return
    store = Store.objects.filter(public_id=spid).first()
    if not store:
        return

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
        _try_db_log(store=store, status="skipped", event_name=event_name, message="no_integration")
        return

    # Gate: respect per-event toggle from IntegrationEventSettings.
    # PageView is always allowed (not in the map).
    # If no settings row exists, all events are allowed by default.
    _EVENT_GATE_MAP = {
        "Purchase": "track_purchase",
        "InitiateCheckout": "track_initiate_checkout",
        "AddToCart": "track_add_to_cart",
        "ViewContent": "track_view_content",
    }
    gate_field = _EVENT_GATE_MAP.get(event_name)
    if gate_field:
        try:
            event_settings = IntegrationEventSettings.objects.filter(
                integration=integration
            ).first()
        except Exception:
            event_settings = None
        if event_settings is not None and not getattr(event_settings, gate_field, True):
            logger.info(
                "tracking.capi_event_gated",
                extra={
                    "store_public_id": spid,
                    "event_name": event_name,
                    "gate_field": gate_field,
                },
            )
            _try_db_log(
                store=store,
                status="skipped",
                event_name=event_name,
                message="gated_by_event_settings",
                metadata={"gate_field": gate_field},
            )
            return

    pixel_id = (getattr(integration, "pixel_id", "") or "").strip()
    access_token = decrypt_value(getattr(integration, "access_token_encrypted", "") or "")
    if not access_token:
        logger.error(
            "tracking.capi_credential_decrypt_failed",
            extra={
                "store_public_id": spid,
                "integration_id": str(integration.public_id),
                "hint": "FIELD_ENCRYPTION_KEY may have been rotated without re-encrypting stored tokens",
            },
        )
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="credential_decrypt_failed",
            metadata={"integration_id": str(integration.public_id), "event_id": event_id},
        )
        return
    if not pixel_id:
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="missing_pixel_id",
            metadata={"event_id": event_id},
        )
        return

    # ---------------------------------------------------------------------
    # Pass-through integrity + pre-send validation
    # ---------------------------------------------------------------------
    if not isinstance(event_id, str) or not event_id:
        _try_db_log(store=store, status="failed", event_name=event_name, message="missing_event_id")
        return
    if not isinstance(payload, dict):
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="pre_send_validation_failed",
            metadata={"reason": "payload_not_object", "event_id": event_id},
        )
        return
    if payload.get("event_id") != event_id:
        logger.warning(
            "tracking.event_id_mismatch",
            extra={
                "store_public_id": spid,
                "event_id_arg": event_id,
                "event_id_payload": payload.get("event_id"),
                "event_name": event_name,
            },
        )
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="pre_send_validation_failed",
            metadata={"reason": "event_id_mismatch", "event_id": event_id},
        )
        return

    if not isinstance(event_name, str) or not event_name:
        _try_db_log(store=store, status="failed", event_name=event_name, message="missing_event_name")
        return
    if payload.get("event_name") != event_name:
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="pre_send_validation_failed",
            metadata={"reason": "event_name_mismatch", "event_id": event_id},
        )
        return
    if event_name not in ALLOWED_EVENT_NAMES:
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="pre_send_validation_failed",
            metadata={"reason": "event_name_not_allowed", "event_id": event_id},
        )
        return

    event_time = payload.get("event_time")
    if not isinstance(event_time, int) or event_time <= 0:
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="pre_send_validation_failed",
            metadata={"reason": "invalid_event_time", "event_id": event_id, "event_time": event_time},
        )
        return

    event_source_url = payload.get("event_source_url") or ""
    user_agent = payload.get("user_agent") or ""
    client_ip = payload.get("client_ip_address") or ""

    fbp = payload.get("fbp")
    fbc = payload.get("fbc")
    if not (isinstance(fbp, str) or fbp is None):
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="pre_send_validation_failed",
            metadata={"reason": "invalid_fbp_type", "event_id": event_id},
        )
        return
    if not (isinstance(fbc, str) or fbc is None):
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="pre_send_validation_failed",
            metadata={"reason": "invalid_fbc_type", "event_id": event_id},
        )
        return

    if not isinstance(user_agent, str) or not user_agent:
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="pre_send_validation_failed",
            metadata={"reason": "missing_user_agent", "event_id": event_id},
        )
        return

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

    # Build contents array from items if present.
    # items is a list of dicts with keys: id, product_id, quantity, item_price/price.
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

    user_data: dict[str, Any] = _build_user_data(payload, client_ip or None)

    if not user_data:
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="pre_send_validation_failed",
            metadata={"reason": "missing_user_data", "event_id": event_id},
        )
        return

    event_payload: dict[str, Any] = {
        "event_name": event_name,
        "event_time": event_time,
        "event_id": event_id,
        "action_source": "website",
        "event_source_url": str(event_source_url or ""),
        "user_data": user_data,
        "custom_data": custom_data,
    }

    body: dict[str, Any] = {
        "data": [event_payload],
        "access_token": access_token,
    }
    test_code = (getattr(integration, "test_event_code", "") or "").strip()
    if test_code:
        body["test_event_code"] = test_code

    url = f"{GRAPH_API_BASE}/{GRAPH_API_VERSION}/{pixel_id}/events"
    try:
        resp = requests.post(url, json=body, timeout=3)
    except (requests.Timeout, requests.ConnectionError) as exc:
        # Network failures are retryable via autoretry_for.
        logger.exception(
            "tracking.capi_network_failure",
            extra={
                "store_public_id": spid,
                "pixel_id": pixel_id,
                "event_name": event_name,
                "event_id": event_id,
            },
        )
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="network_failure",
            metadata={"pixel_id": pixel_id, "event_id": event_id},
        )
        raise

    if resp.status_code >= 400:
        classification, meta = _classify_meta_http_failure(resp)
        logger.warning(
            "tracking.capi_meta_rejected",
            extra={
                "store_public_id": spid,
                "pixel_id": pixel_id,
                "event_name": event_name,
                "event_id": event_id,
                "classification": classification,
                "http_status": resp.status_code,
            },
        )
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message=classification,
            metadata={
                "pixel_id": pixel_id,
                "event_id": event_id,
                "classification": classification,
                **meta,
            },
        )
        return

    # Optional: capture Meta response IDs for debugging.
    resp_body = _safe_json(resp) or {}

    _try_db_log(
        store=store,
        status="success",
        event_name=event_name,
        message="",
        metadata={"pixel_id": pixel_id, "event_id": event_id, "meta_response": resp_body},
    )


@app.task(
    name="engine.apps.tracking.cleanup_old_event_logs",
    soft_time_limit=120,
    time_limit=150,
)
def cleanup_old_event_logs() -> int:
    """Celery beat: delete StoreEventLog rows older than EVENT_LOG_RETENTION_HOURS (app=tracking only)."""
    from datetime import timedelta

    from engine.apps.marketing_integrations.models import StoreEventLog

    cutoff = timezone.now() - timedelta(hours=EVENT_LOG_RETENTION_HOURS)
    qs = StoreEventLog.objects.filter(created_at__lt=cutoff, app="tracking")
    deleted, _ = qs.delete()
    return int(deleted or 0)

