from __future__ import annotations

import hashlib

from django.conf import settings
from django.core.cache import caches
from django.http import HttpRequest, JsonResponse
from django.utils.deprecation import MiddlewareMixin

from engine.apps.stores.services import (
    resolve_active_store_api_key,
    touch_store_api_key_last_used,
)

API_KEY_EXEMPT_PATHS = (
    "/api/v1/auth/",
    "/api/v1/admin/",
    "/api/v1/stores/",
    "/api/v1/system-notifications/",
    "/api/v1/settings/network/",
)
API_KEY_EXACT_EXEMPT_PATHS = (
    "/api/v1/health",
    "/api/v1/health/",
)


def _normalized_path(path: str) -> str:
    normalized = (path or "").strip()
    if not normalized:
        return "/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def _normalized_prefix(prefix: str) -> str:
    normalized = _normalized_path(prefix).rstrip("/")
    return f"{normalized}/"


def _path_starts_with_prefix(path: str, prefix: str) -> bool:
    normalized_path = _normalized_path(path)
    normalized_prefix = _normalized_prefix(prefix)
    if normalized_path == normalized_prefix[:-1]:
        return True
    return normalized_path.startswith(normalized_prefix)


def _rate_limit_cache():
    alias = getattr(settings, "TENANT_RATE_LIMIT_CACHE_ALIAS", "default")
    return caches[alias]


def _throttle_invalid_api_key(raw_key: str | None) -> bool:
    if not raw_key:
        return False
    limit = int(getattr(settings, "TENANT_INVALID_API_KEY_RATE_LIMIT_PER_MIN", 60))
    if limit <= 0:
        return False
    fingerprint = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    cache_key = f"rate:invalid_api_key:{fingerprint}"
    c = _rate_limit_cache()
    try:
        current = c.incr(cache_key)
    except ValueError:
        c.set(cache_key, 1, 60)
        current = 1
    return current > limit


def _extract_bearer_token(request: HttpRequest) -> str | None:
    header = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def resolve_request_api_key(request: HttpRequest):
    raw_api_key = _extract_bearer_token(request)
    if not raw_api_key:
        return None
    return resolve_active_store_api_key(raw_api_key)


def requires_tenant_api_key(path: str) -> bool:
    normalized_path = _normalized_path(path)
    if normalized_path in API_KEY_EXACT_EXEMPT_PATHS:
        return False
    if any(_path_starts_with_prefix(normalized_path, prefix) for prefix in API_KEY_EXEMPT_PATHS):
        return False
    prefix = _normalized_prefix(getattr(settings, "TENANT_API_PREFIX", "/api/v1/"))
    if not _path_starts_with_prefix(normalized_path, prefix):
        return False
    return True


class TenantApiKeyMiddleware(MiddlewareMixin):
    """
    Enforce API-key tenant resolution on non-admin API routes.
    """

    def process_request(self, request: HttpRequest):
        if not bool(getattr(settings, "TENANT_API_KEY_ENFORCE", True)):
            return None
        if request.method == "OPTIONS":
            return None

        path = request.path
        if not requires_tenant_api_key(path):
            return None

        key_row = resolve_request_api_key(request)
        if key_row is None:
            raw_api_key = _extract_bearer_token(request)
            if _throttle_invalid_api_key(raw_api_key):
                response = JsonResponse({"detail": "Too many invalid API key attempts."}, status=429)
                response["Retry-After"] = "60"
                return response
            return JsonResponse({"detail": "Invalid API key."}, status=401)

        request.api_key = key_row
        request.store = key_row.store
        touch_store_api_key_last_used(key_row)
        return None
