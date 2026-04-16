from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

from django.core.cache import cache
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from engine.apps.stores.models import StoreApiKey
from engine.apps.stores.services import (
    get_request_store_settings_row,
    resolve_active_store_api_key,
    touch_store_api_key_last_used,
)
from engine.core import cache_service
from engine.apps.tracking.ip import client_ip_from_request
from engine.apps.tracking.serializers import TrackingEventIngestSerializer

logger = logging.getLogger(__name__)


def _bearer_token_from_headers(request) -> str | None:
    header = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = (parts[1] or "").strip()
    return token or None


def _origin_host(request) -> str | None:
    origin = (request.headers.get("Origin") or request.headers.get("origin") or "").strip()
    if not origin:
        return None
    try:
        p = urlparse(origin)
        return (p.hostname or "").strip().lower() or None
    except Exception:
        return None


def _domain_allowed_for_store(request, store) -> bool:
    """
    Secondary safety check: validate Origin host against store allowlist when present.

    This is intentionally **opt-in**: when a store has no allowlist configured, we allow.
    """

    host = _origin_host(request)
    if not host:
        return True

    row = get_request_store_settings_row(request, store)
    public = (getattr(row, "storefront_public", None) or {}) if row else {}
    if not isinstance(public, dict):
        return True

    allowed = public.get("allowed_domains") or public.get("allowedDomain") or public.get("allowed_origins")
    if not allowed:
        return True
    if isinstance(allowed, str):
        allowed = [allowed]
    if not isinstance(allowed, list):
        return True

    normalized = {str(x).strip().lower() for x in allowed if str(x).strip()}
    if not normalized:
        return True
    return host in normalized


class TrackingEventIngestView(APIView):
    """
    tracker.js event ingestion endpoint.

    Responsibilities:
    - Resolve tenant from publishable API key (Bearer ak_pk_...)
    - Validate strict schema
    - Add server-derived client_ip_address
    - Enqueue Celery task (no Meta calls here)
    """

    authentication_classes: list = []
    permission_classes: list = []
    # Allow publishable API keys on this non-/api/v1/ endpoint (enforced explicitly in-view).
    allow_api_key = True

    def post(self, request):
        store_public_id_for_log = None
        event_name_for_log = None
        event_id_for_log = None
        outcome_status = "rejected"
        outcome_reason = None

        token = _bearer_token_from_headers(request)
        if not token or not token.startswith("ak_pk_"):
            outcome_reason = "missing_api_key"
            logger.info(
                json.dumps(
                    {
                        "store_id": store_public_id_for_log,
                        "event_name": event_name_for_log,
                        "event_id": event_id_for_log,
                        "status": outcome_status,
                        "reason": outcome_reason,
                    },
                    separators=(",", ":"),
                )
            )
            return Response({"detail": "Storefront API key required."}, status=status.HTTP_401_UNAUTHORIZED)

        key_row = resolve_active_store_api_key(token)
        if key_row is None:
            outcome_reason = "invalid_api_key"
            logger.info(
                json.dumps(
                    {
                        "store_id": store_public_id_for_log,
                        "event_name": event_name_for_log,
                        "event_id": event_id_for_log,
                        "status": outcome_status,
                        "reason": outcome_reason,
                    },
                    separators=(",", ":"),
                )
            )
            return Response({"detail": "Invalid API key."}, status=status.HTTP_401_UNAUTHORIZED)
        if getattr(key_row, "key_type", None) != StoreApiKey.KeyType.PUBLIC:
            outcome_reason = "non_public_api_key"
            logger.info(
                json.dumps(
                    {
                        "store_id": store_public_id_for_log,
                        "event_name": event_name_for_log,
                        "event_id": event_id_for_log,
                        "status": outcome_status,
                        "reason": outcome_reason,
                    },
                    separators=(",", ":"),
                )
            )
            return Response({"detail": "Publishable API key required."}, status=status.HTTP_403_FORBIDDEN)

        store = getattr(key_row, "store", None)
        if store is None or not getattr(store, "is_active", False):
            store_public_id_for_log = getattr(store, "public_id", None) if store else None
            outcome_reason = "store_inactive"
            logger.info(
                json.dumps(
                    {
                        "store_id": store_public_id_for_log,
                        "event_name": event_name_for_log,
                        "event_id": event_id_for_log,
                        "status": outcome_status,
                        "reason": outcome_reason,
                    },
                    separators=(",", ":"),
                )
            )
            return Response({"detail": "Store inactive."}, status=status.HTTP_403_FORBIDDEN)

        store_public_id_for_log = getattr(store, "public_id", None)

        if not _domain_allowed_for_store(request, store):
            outcome_reason = "origin_not_allowed"
            logger.info(
                json.dumps(
                    {
                        "store_id": store_public_id_for_log,
                        "event_name": event_name_for_log,
                        "event_id": event_id_for_log,
                        "status": outcome_status,
                        "reason": outcome_reason,
                    },
                    separators=(",", ":"),
                )
            )
            return Response({"detail": "Origin not allowed for store."}, status=status.HTTP_403_FORBIDDEN)

        raw_event_id = request.data.get("event_id")
        raw_event_name = request.data.get("event_name")

        ser = TrackingEventIngestSerializer(data=request.data)
        try:
            ser.is_valid(raise_exception=True)
        except Exception:
            # Best-effort structured rejection log even when DRF raises.
            event_name_for_log = raw_event_name if isinstance(raw_event_name, str) else None
            event_id_for_log = raw_event_id if isinstance(raw_event_id, str) else None
            outcome_reason = "validation_failed"
            logger.info(
                json.dumps(
                    {
                        "store_id": store_public_id_for_log,
                        "event_name": event_name_for_log,
                        "event_id": event_id_for_log,
                        "status": outcome_status,
                        "reason": outcome_reason,
                    },
                    separators=(",", ":"),
                )
            )
            raise
        data = dict(ser.validated_data)

        event_name_for_log = data.get("event_name")
        event_id_for_log = data.get("event_id")

        # Guard: event_id must not be mutated anywhere in the ingest pipeline.
        if raw_event_id != event_id_for_log:
            logger.warning(
                "tracking.event_id_mutation_detected",
                extra={
                    "store_public_id": store_public_id_for_log,
                    "raw_event_id": raw_event_id,
                    "validated_event_id": event_id_for_log,
                    "event_name": event_name_for_log,
                },
            )
            outcome_reason = "event_id_mutated"
            logger.info(
                json.dumps(
                    {
                        "store_id": store_public_id_for_log,
                        "event_name": event_name_for_log,
                        "event_id": event_id_for_log,
                        "status": outcome_status,
                        "reason": outcome_reason,
                    },
                    separators=(",", ":"),
                )
            )
            return Response({"detail": "Invalid event_id."}, status=status.HTTP_400_BAD_REQUEST)

        # Lightweight replay protection: ignore duplicates for same store + event_id within 10s.
        dedupe_key = cache_service.build_key(store_public_id_for_log, "tracking_ingest_dedupe", event_id_for_log)
        try:
            first_seen = cache.add(dedupe_key, "1", timeout=10)
        except Exception:
            first_seen = True
        if not first_seen:
            outcome_status = "ignored"
            outcome_reason = "replay"
            logger.info(
                json.dumps(
                    {
                        "store_id": store_public_id_for_log,
                        "event_name": event_name_for_log,
                        "event_id": event_id_for_log,
                        "status": outcome_status,
                        "reason": outcome_reason,
                    },
                    separators=(",", ":"),
                )
            )
            return Response({"status": "ignored"}, status=status.HTTP_200_OK)

        ip = client_ip_from_request(request)
        if ip:
            data["client_ip_address"] = ip

        # Best-effort usage timestamp update
        try:
            touch_store_api_key_last_used(key_row)
        except Exception:
            pass

        from engine.apps.tracking.tasks import send_capi_event

        try:
            send_capi_event.delay(
                store.public_id,
                data["event_name"],
                data["event_id"],
                data,
            )
        except Exception:
            logger.exception(
                "tracking.enqueue_failed",
                extra={
                    "store_public_id": getattr(store, "public_id", None),
                    "event_name": data.get("event_name"),
                    "event_id": data.get("event_id"),
                },
            )
            outcome_reason = "enqueue_failed"
            logger.info(
                json.dumps(
                    {
                        "store_id": store_public_id_for_log,
                        "event_name": event_name_for_log,
                        "event_id": event_id_for_log,
                        "status": outcome_status,
                        "reason": outcome_reason,
                    },
                    separators=(",", ":"),
                )
            )
            return Response({"detail": "Failed to enqueue."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        outcome_status = "queued"
        logger.info(
            json.dumps(
                {
                    "store_id": store_public_id_for_log,
                    "event_name": event_name_for_log,
                    "event_id": event_id_for_log,
                    "status": outcome_status,
                },
                separators=(",", ":"),
            )
        )
        return Response({"status": "queued"}, status=status.HTTP_200_OK)

