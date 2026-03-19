from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.http import HttpRequest
from django.conf import settings
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
    """Resolve a Store instance from the incoming Host header."""
    host = _normalize_host(request.get_host())
    if not host:
        return None
    platform_hosts = {h.lower() for h in getattr(settings, "PLATFORM_HOSTS", [])}
    if host in platform_hosts:
        return None
    try:
        return Store.objects.exclude(domain__isnull=True).get(
            domain__iexact=host, is_active=True
        )
    except Store.DoesNotExist:
        return None


def get_active_store(request: HttpRequest) -> ActiveStoreContext:
    """
    Resolve the active store for the current request.

    Priority:
    1) Explicit X-Store-ID header (for dashboard / tools)
    2) JWT claim `active_store_id` (if present on the user)
    3) Store derived from the host via middleware.
    """
    store: Optional[Store] = None
    membership: Optional[StoreMembership] = None

    # 1) Explicit header — try public_id first, fall back to integer PK for backward compat
    header_store_id = request.headers.get("X-Store-ID") or request.headers.get("x-store-id")
    if header_store_id:
        try:
            store = Store.objects.get(public_id=header_store_id, is_active=True)
        except (Store.DoesNotExist, ValueError):
            # Backward-compat: allow integer PK during frontend migration window
            try:
                store = Store.objects.get(pk=int(header_store_id), is_active=True)
            except (Store.DoesNotExist, ValueError, TypeError):
                store = None

    # 2) JWT claim `active_store_id`
    # In DRF SimpleJWT, the validated token is available as `request.auth`.
    if store is None and getattr(request, "auth", None):
        active_store_id = request.auth.get("active_store_id")  # type: ignore[union-attr]
        if active_store_id:
            try:
                store = Store.objects.get(public_id=active_store_id, is_active=True)
            except (Store.DoesNotExist, ValueError):
                # Backward-compat: allow integer PK during frontend migration window
                try:
                    store = Store.objects.get(pk=int(active_store_id), is_active=True)
                except (Store.DoesNotExist, ValueError, TypeError):
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

