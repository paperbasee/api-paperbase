from __future__ import annotations

import json
import logging
from typing import Any

import requests
from django.utils import timezone

from config.celery import app
from engine.core.encryption import decrypt_value
from engine.apps.tracking.contract import ALLOWED_EVENT_NAMES

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v18.0"
GRAPH_API_BASE = "https://graph.facebook.com"


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

    from engine.apps.marketing_integrations.models import MarketingIntegration
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

    pixel_id = (getattr(integration, "pixel_id", "") or "").strip()
    access_token = decrypt_value(getattr(integration, "access_token_encrypted", "") or "")
    if not pixel_id or not access_token:
        _try_db_log(
            store=store,
            status="failed",
            event_name=event_name,
            message="missing_credentials",
            metadata={"pixel_id": pixel_id, "event_id": event_id},
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

    custom_data: dict[str, Any] = {
        "value": payload.get("value", 0) or 0,
        "currency": payload.get("currency", "BDT") or "BDT",
        "content_ids": payload.get("content_ids") or [],
        "content_type": payload.get("content_type", "product") or "product",
    }

    user_data: dict[str, Any] = {
        "client_user_agent": user_agent,
    }
    if client_ip:
        user_data["client_ip_address"] = client_ip
    if isinstance(fbp, str) and fbp:
        user_data["fbp"] = fbp
    if isinstance(fbc, str) and fbc:
        user_data["fbc"] = fbc

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
    """Celery beat: delete StoreEventLog rows older than 1 hour (app=tracking only)."""
    from datetime import timedelta

    from engine.apps.marketing_integrations.models import StoreEventLog

    cutoff = timezone.now() - timedelta(hours=1)
    qs = StoreEventLog.objects.filter(created_at__lt=cutoff, app="tracking")
    deleted, _ = qs.delete()
    return int(deleted or 0)

