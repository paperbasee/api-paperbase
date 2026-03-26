"""Rate limits for API-key authenticated tenant requests (HTTP + WebSocket)."""

from __future__ import annotations

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import caches
from django.http import HttpRequest, JsonResponse
from django.utils.deprecation import MiddlewareMixin

from engine.core.store_api_key_auth import requires_tenant_api_key
from engine.core.ws_api_key import resolve_scope_api_key


def _rate_limit_cache():
    alias = getattr(settings, "TENANT_RATE_LIMIT_CACHE_ALIAS", "default")
    return caches[alias]


def _rate_window_seconds() -> int:
    return 60


def _incr_under_limit(cache_key: str, limit: int) -> bool:
    """
    Increment a fixed-window counter. Returns True if under or at limit, False if exceeded.
    """
    if limit <= 0:
        return True
    window = _rate_window_seconds()
    c = _rate_limit_cache()
    try:
        n = c.incr(cache_key)
    except ValueError:
        c.set(cache_key, 1, window)
        n = 1
    return n <= limit


def api_key_rate_check(api_key_id: str) -> bool:
    """
    Apply per-api-key fixed-window limit.
    """
    per_key_limit = int(getattr(settings, "TENANT_API_KEY_RATE_LIMIT_PER_MIN", 600))
    return _incr_under_limit(f"rate:api_key:{api_key_id}", per_key_limit)


class ApiKeyRateLimitMiddleware(MiddlewareMixin):
    """
    Rate-limit tenant endpoints by API key identity.
    """

    def process_request(self, request: HttpRequest):
        path = request.path
        if not requires_tenant_api_key(path):
            return None
        key_row = getattr(request, "api_key", None)
        if key_row is None:
            return None
        if not api_key_rate_check(str(key_row.public_id)):
            response = JsonResponse({"detail": "Too many requests."}, status=429)
            response["Retry-After"] = str(_rate_window_seconds())
            return response
        return None


class WebSocketApiKeyRateLimitMiddleware:
    """ASGI: rate-limit websocket handshakes per API key."""

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
        if not api_key_public_id:
            await send({"type": "websocket.close", "code": 1008})
            return
        allowed = await sync_to_async(api_key_rate_check)(api_key_public_id)
        if not allowed:
            await send({"type": "websocket.close", "code": 1008})
            return
        return await self.inner(scope, receive, send)
