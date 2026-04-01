from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.core.cache import caches
from django.http import HttpRequest, JsonResponse
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils.deprecation import MiddlewareMixin

from config.permissions import IsStorefrontAPIKey
from engine.apps.stores.services import (
    resolve_active_store_api_key,
    touch_store_api_key_last_used,
)
from engine.core.redis_fixed_window import fixed_window_increment

logger = logging.getLogger(__name__)

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

TENANT_API_KEY_REQUIRED_DETAIL = (
    "No API key found. Create one in Settings → Networking."
)

# Kept for tests/documentation only; authorization is enforced via permission classes.
STORE_FRONTEND_ROUTE_POLICY = (
    ("/api/v1/products/", {"GET"}),
    ("/api/v1/catalog/", {"GET"}),
    ("/api/v1/store/", {"GET"}),
    ("/api/v1/categories/", {"GET"}),
    ("/api/v1/banners/", {"GET"}),
    ("/api/v1/notifications/", {"GET"}),
    ("/api/v1/shipping/options/", {"GET"}),
    ("/api/v1/shipping/zones/", {"GET"}),
    ("/api/v1/shipping/preview/", {"POST"}),
    ("/api/v1/orders/initiate-checkout/", {"POST"}),
    ("/api/v1/orders/", {"POST"}),
    ("/api/v1/support/tickets/", {"POST"}),
    ("/api/v1/pricing/", {"POST"}),
    ("/api/v1/search/", {"GET"}),
)
_API_KEY_VIEW_SCAN_DONE = False


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
        current = fixed_window_increment(c, cache_key, 60)
    except Exception:
        logger.warning(
            "invalid_api_key rate limit cache error; fail open",
            exc_info=True,
            extra={"rate_limit_key": cache_key},
        )
        return False
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
    if not _is_api_key_token(raw_api_key):
        return None
    return resolve_active_store_api_key(raw_api_key)


def _is_api_key_token(raw_token: str | None) -> bool:
    token = (raw_token or "").strip()
    return token.startswith("ak_pk_") or token.startswith("ak_sk_")


def _is_admin_order_read_path(path: str, method: str) -> bool:
    normalized_path = _normalized_path(path)
    if (method or "GET").upper() != "GET":
        return False
    if normalized_path in {"/api/v1/orders", "/api/v1/orders/"}:
        return True
    return _path_starts_with_prefix(normalized_path, "/api/v1/orders/")


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


def _iter_urlpatterns(patterns):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _iter_urlpatterns(pattern.url_patterns)
        elif isinstance(pattern, URLPattern):
            yield pattern


def validate_storefront_api_key_view_flags(*, patterns=None) -> None:
    url_patterns = patterns if patterns is not None else get_resolver().url_patterns
    missing_allow_flag = []
    for pattern in _iter_urlpatterns(url_patterns):
        callback = getattr(pattern, "callback", None)
        view_class = getattr(callback, "view_class", None) or getattr(callback, "cls", None)
        if not view_class:
            continue
        permission_classes = getattr(view_class, "permission_classes", []) or []
        if IsStorefrontAPIKey not in permission_classes:
            continue
        if not getattr(view_class, "allow_api_key", False):
            missing_allow_flag.append(view_class.__name__)
    if missing_allow_flag:
        names = ", ".join(sorted(set(missing_allow_flag)))
        raise RuntimeError(
            "Storefront API key views must declare allow_api_key=True. "
            f"Missing on: {names}"
        )


def maybe_validate_storefront_api_key_view_flags() -> None:
    global _API_KEY_VIEW_SCAN_DONE
    if _API_KEY_VIEW_SCAN_DONE:
        return
    validate_storefront_api_key_view_flags()
    _API_KEY_VIEW_SCAN_DONE = True


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
        raw_api_key = _extract_bearer_token(request)
        if _is_admin_order_read_path(path, request.method) and not _is_api_key_token(raw_api_key):
            return None

        if not requires_tenant_api_key(path):
            # Fail closed: API-key credentials are never valid on exempt/system routes.
            if _is_api_key_token(raw_api_key):
                return JsonResponse({"detail": "API key cannot access this endpoint."}, status=403)
            return None

        key_row = resolve_request_api_key(request)
        if key_row is None:
            if _throttle_invalid_api_key(raw_api_key):
                response = JsonResponse({"detail": "Too many invalid API key attempts."}, status=429)
                response["Retry-After"] = "60"
                return response
            return JsonResponse({"detail": TENANT_API_KEY_REQUIRED_DETAIL}, status=401)
        if key_row.key_type != key_row.KeyType.PUBLIC:
            return JsonResponse({"detail": "Secret API keys cannot access storefront endpoints."}, status=403)

        request.api_key = key_row
        request.store = key_row.store
        touch_store_api_key_last_used(key_row)
        return None

    def process_view(self, request: HttpRequest, view_func, view_args, view_kwargs):
        maybe_validate_storefront_api_key_view_flags()
        if not getattr(request, "api_key", None):
            return None
        view_class = getattr(view_func, "view_class", None) or getattr(view_func, "cls", None)
        allow_api_key = bool(getattr(view_class, "allow_api_key", False))
        if allow_api_key:
            return None
        return JsonResponse({"detail": "API key cannot access this endpoint."}, status=403)

    def process_response(self, request: HttpRequest, response):
        return response
