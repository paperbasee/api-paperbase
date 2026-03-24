from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils.deprecation import MiddlewareMixin

from engine.apps.stores.models import Store, StoreMembership


@dataclass
class ActiveStoreContext:
    store: Optional[Store]
    membership: Optional[StoreMembership]


def _normalize_host(host: str) -> str:
    """Normalize host header (strip port)."""
    if not host:
        return ""
    return host.split(":", 1)[0].lower()


def resolve_store_from_host(request: HttpRequest) -> Optional[Store]:
    """Resolve a Store instance from the incoming Host header via verified Domain rows."""
    from engine.core.domain_resolution_cache import resolve_store_from_host_cached

    host = _normalize_host(request.get_host())
    if not host:
        return None
    platform_hosts = {h.lower() for h in getattr(settings, "PLATFORM_HOSTS", [])}
    if host in platform_hosts:
        return None
    return resolve_store_from_host_cached(host)


def get_active_store(request: HttpRequest) -> ActiveStoreContext:
    """
    Resolve the active store for the current request.

    Priority:
    1) Explicit X-Store-Public-ID header (for dashboard / tools)
    2) JWT claim `active_store_public_id` (if present on the user)
    3) Store derived from the host via middleware.
    """
    store: Optional[Store] = None
    membership: Optional[StoreMembership] = None

    # 1) Explicit header — public_id only.
    header_store_public_id = request.headers.get("X-Store-Public-ID") or request.headers.get("x-store-public-id")
    if header_store_public_id:
        try:
            store = Store.objects.get(public_id=header_store_public_id, is_active=True)
        except (Store.DoesNotExist, ValueError):
            store = None

    # 2) JWT claim `active_store_public_id` — public_id only.
    # In DRF SimpleJWT, the validated token is available as `request.auth`.
    if store is None and getattr(request, "auth", None):
        active_store_public_id = request.auth.get("active_store_public_id")  # type: ignore[union-attr]
        if active_store_public_id:
            try:
                store = Store.objects.get(public_id=active_store_public_id, is_active=True)
            except (Store.DoesNotExist, ValueError):
                store = None

    # 3) Fallback to host-resolved store (set by middleware)
    if store is None:
        store = getattr(request, "store", None)

    if store and getattr(request.user, "is_authenticated", False):
        try:
            membership = StoreMembership.objects.get(
                user=request.user,
                store=store,
                is_active=True,
            )
        except StoreMembership.DoesNotExist:
            membership = None

    return ActiveStoreContext(store=store, membership=membership)


def require_resolved_store(request: HttpRequest) -> None:
    """
    DRF storefront views: require a resolved store (host, X-Store-Public-ID, or JWT active_store_public_id).
    Raises PermissionDenied with the same message as TenantApiGuardMiddleware.
    """
    from rest_framework.exceptions import PermissionDenied

    ctx = get_active_store(request)
    if ctx.store is None:
        raise PermissionDenied(detail="Unknown tenant host.")


class TenantResolutionMiddleware(MiddlewareMixin):
    """
    Middleware that attaches `request.store` based on the Host header.

    This is lightweight and safe to run for all requests.
    """

    def process_request(self, request: HttpRequest) -> None:
        if hasattr(request, "store") or hasattr(request, "is_platform_request"):
            return
        host = _normalize_host(request.get_host())
        platform_hosts = {h.lower() for h in getattr(settings, "PLATFORM_HOSTS", [])}
        request.is_platform_request = host in platform_hosts
        request.store = None if request.is_platform_request else resolve_store_from_host(request)


class TenantApiGuardMiddleware(MiddlewareMixin):
    """
    On tenant hosts, require a resolved store for tenant-scoped API routes.

    Platform hosts (dashboard/API on PLATFORM_HOSTS) skip this check.
    Exempt paths (e.g. auth) allow requests without host-based tenant resolution.
    """

    def process_request(self, request: HttpRequest):
        if getattr(request, "is_platform_request", False):
            return None
        if request.method == "OPTIONS":
            return None
        path = request.path
        prefix = getattr(settings, "TENANT_API_PREFIX", "/api/v1/")
        exempt = getattr(settings, "TENANT_API_EXEMPT_PREFIXES", ())
        if not path.startswith(prefix):
            return None
        if any(path.startswith(e) for e in exempt):
            return None
        # Dashboard APIs: tenant comes from X-Store-Public-ID / JWT, not Host.
        if path.startswith("/api/v1/admin/"):
            return None
        if path.startswith("/api/v1/stores/"):
            return None
        # Accept tenant context resolved from Host, X-Store-Public-ID, or JWT claim.
        if get_active_store(request).store is None:
            return JsonResponse({"detail": "Unknown tenant host."}, status=403)
        return None


def resolve_store_public_id_from_host_header(host: str) -> Optional[str]:
    """WebSocket: return store public_id for verified domain host, or None."""
    from engine.core.domain_resolution_cache import (
        resolve_store_public_id_from_host_cached,
    )

    host = _normalize_host(host)
    if not host:
        return None
    platform_hosts = {h.lower() for h in getattr(settings, "PLATFORM_HOSTS", [])}
    if host in platform_hosts:
        return None
    return resolve_store_public_id_from_host_cached(host)
