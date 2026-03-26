"""API-key authentication middleware for store websocket connections."""

from __future__ import annotations

from urllib.parse import parse_qs

from asgiref.sync import sync_to_async

from engine.apps.stores.services import (
    resolve_active_store_api_key,
    touch_store_api_key_last_used,
)


def _extract_ws_api_key(scope) -> str | None:
    headers = dict(scope.get("headers", []))
    header = headers.get(b"authorization", b"").decode("latin1")
    if header:
        parts = header.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
            if token:
                return token
    qs = parse_qs(scope.get("query_string", b"").decode("utf-8"))
    return (qs.get("api_key") or [None])[0]


async def resolve_scope_api_key(scope) -> bool:
    """
    Resolve API key once and attach canonical store/api_key scope context.
    """
    existing_api_key = scope.get("api_key")
    existing_store = scope.get("store")
    if existing_api_key is not None and existing_store is not None:
        return True

    raw_key = _extract_ws_api_key(scope)
    if not raw_key:
        return False
    key_row = await sync_to_async(resolve_active_store_api_key)(raw_key)
    if key_row is None:
        return False
    scope["api_key"] = key_row
    scope["store"] = key_row.store
    scope["api_key_public_id"] = key_row.public_id
    scope["store_public_id"] = key_row.store.public_id
    scope["store_id"] = key_row.store_id
    await sync_to_async(touch_store_api_key_last_used)(key_row)
    return True


class APIKeyWebSocketMiddleware:
    """Attach store context to websocket scope from API key."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            return await self.inner(scope, receive, send)
        ok = await resolve_scope_api_key(scope)
        if not ok:
            await send({"type": "websocket.close", "code": 4401})
            return
        return await self.inner(scope, receive, send)
