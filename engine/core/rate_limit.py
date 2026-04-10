"""Rate limits for API-key authenticated tenant requests (HTTP + WebSocket)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import caches
from django.http import HttpRequest, JsonResponse
from django.utils.deprecation import MiddlewareMixin

from engine.apps.billing.feature_gate import get_feature_config
from engine.apps.stores.services import get_store_owner_user
from engine.core.client_ip import get_client_ip
from engine.core.redis_fixed_window import incr_under_limit
from engine.core.store_api_key_auth import requires_tenant_api_key
from engine.core.ws_api_key import resolve_scope_api_key

if TYPE_CHECKING:
    from engine.apps.stores.models import Store

logger = logging.getLogger(__name__)


def _rate_limit_cache():
    alias = getattr(settings, "TENANT_RATE_LIMIT_CACHE_ALIAS", "default")
    return caches[alias]


def _rate_window_seconds() -> int:
    return 60


def _per_ip_limit() -> int:
    return int(getattr(settings, "TENANT_STOREFRONT_RATE_LIMIT_PER_IP_PER_MIN", 100))


def _settings_aggregate_fallback() -> int:
    return int(getattr(settings, "TENANT_API_KEY_AGGREGATE_RATE_LIMIT_PER_MIN", 5000))


def resolve_storefront_aggregate_limit(store: Store) -> int:
    """
    Max storefront API requests per minute (per API key) from the store owner's plan.

    Uses Plan.features.limits['storefront_requests_per_minute'] when set and positive;
    falls back to legacy key storefront_aggregate_rpm; otherwise
    TENANT_API_KEY_AGGREGATE_RATE_LIMIT_PER_MIN.
    """
    fallback = _settings_aggregate_fallback()
    owner = get_store_owner_user(store)
    if not owner:
        return fallback
    cfg = get_feature_config(owner)
    limits = cfg.get("limits") or {}
    raw = limits.get("storefront_requests_per_minute")
    if raw is None:
        raw = limits.get("storefront_aggregate_rpm")
    n: int | None = None
    if isinstance(raw, int) and raw > 0:
        n = raw
    elif isinstance(raw, str):
        try:
            v = int(raw.strip())
            if v > 0:
                n = v
        except ValueError:
            n = None
    return n if n is not None else fallback


def storefront_rate_check(
    *,
    store: Store,
    store_public_id: str,
    api_key_public_id: str,
    client_ip: str,
) -> tuple[bool, str | None]:
    """
    Apply per-(store,IP) and per–API-key aggregate fixed-window limits.

    Returns (allowed, reason) where reason is ``per_ip``, ``aggregate``, or None.
    """
    window = _rate_window_seconds()
    c = _rate_limit_cache()
    ip_key = f"rate:store:{store_public_id}:ip:{client_ip}"
    agg_key = f"rate:api_key:{api_key_public_id}"
    aggregate_limit = resolve_storefront_aggregate_limit(store)

    if not incr_under_limit(
        c, ip_key, window, _per_ip_limit(), fail_open_on_cache_error=False
    ):
        return False, "per_ip"
    if not incr_under_limit(
        c, agg_key, window, aggregate_limit, fail_open_on_cache_error=False
    ):
        return False, "aggregate"
    return True, None


def _ws_client_ip(scope) -> str:
    client = scope.get("client")
    if isinstance(client, (list, tuple)) and client:
        return str(client[0] or "").strip() or "unknown"
    return "unknown"


class ApiKeyRateLimitMiddleware(MiddlewareMixin):
    """
    Rate-limit tenant endpoints: per store + client IP, plus aggregate per API key.
    """

    def process_request(self, request: HttpRequest):
        path = request.path
        if not requires_tenant_api_key(path):
            return None
        key_row = getattr(request, "api_key", None)
        if key_row is None:
            return None
        store = getattr(key_row, "store", None)
        if store is None:
            return None

        client_ip = get_client_ip(request)
        store_public_id = str(store.public_id)
        api_key_public_id = str(key_row.public_id)
        ok, reason = storefront_rate_check(
            store=store,
            store_public_id=store_public_id,
            api_key_public_id=api_key_public_id,
            client_ip=client_ip,
        )
        if not ok:
            logger.warning(
                "storefront rate limit exceeded (429)",
                extra={
                    "store_public_id": store_public_id,
                    "client_ip": client_ip,
                    "api_key_public_id": api_key_public_id,
                    "rate_limit_reason": reason,
                },
            )
            response = JsonResponse({"detail": "Too many requests."}, status=429)
            response["Retry-After"] = str(_rate_window_seconds())
            return response
        return None


class WebSocketApiKeyRateLimitMiddleware:
    """ASGI: rate-limit websocket handshakes per store+IP and per API key aggregate."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            return await self.inner(scope, receive, send)
        ok = await resolve_scope_api_key(scope)
        if not ok:
            await send({"type": "websocket.close", "code": 1008})
            return
        api_key_public_id = str(scope.get("api_key_public_id") or "")
        store_public_id = str(scope.get("store_public_id") or "")
        store = scope.get("store")
        if not api_key_public_id or not store_public_id or store is None:
            await send({"type": "websocket.close", "code": 1008})
            return
        client_ip = _ws_client_ip(scope)
        allowed, reason = await sync_to_async(storefront_rate_check)(
            store=store,
            store_public_id=store_public_id,
            api_key_public_id=api_key_public_id,
            client_ip=client_ip,
        )
        if not allowed:
            logger.warning(
                "storefront websocket rate limit denied (close 1008)",
                extra={
                    "store_public_id": store_public_id,
                    "client_ip": client_ip,
                    "api_key_public_id": api_key_public_id,
                    "rate_limit_reason": reason,
                },
            )
            await send({"type": "websocket.close", "code": 1008})
            return
        return await self.inner(scope, receive, send)
