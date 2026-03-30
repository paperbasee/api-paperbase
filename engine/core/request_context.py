from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.http import HttpRequest

if TYPE_CHECKING:
    from engine.apps.stores.models import Store


@dataclass(frozen=True)
class RequestContext:
    """HTTP request execution context: tenant isolation vs platform-wide access."""

    tenant: Store | None = None
    is_platform_admin: bool = False


_store_settings_request_cache: ContextVar[dict[int, Any] | None] = ContextVar(
    "_store_settings_request_cache", default=None
)
_branding_request_cache: ContextVar[dict[str, Any] | None] = ContextVar(
    "_branding_request_cache", default=None
)


def reset_request_scoped_caches() -> None:
    """Clear per-request branding/settings dicts (call at request boundaries)."""
    _store_settings_request_cache.set(None)
    _branding_request_cache.set(None)


def get_store_settings_request_cache() -> dict[int, Any]:
    m = _store_settings_request_cache.get()
    if m is None:
        m = {}
        _store_settings_request_cache.set(m)
    return m


def get_branding_request_cache() -> dict[str, Any]:
    m = _branding_request_cache.get()
    if m is None:
        m = {}
        _branding_request_cache.set(m)
    return m


def get_dashboard_store_from_request(request: HttpRequest) -> "Store | None":
    """
    Active store for dashboard/admin views: use tenant from middleware if present,
    else fall back to get_active_store (e.g. tests or unusual entrypoints).
    """
    ctx = getattr(request, "context", None)
    if ctx is not None and not ctx.is_platform_admin and ctx.tenant is not None:
        return ctx.tenant
    from engine.core.tenancy import get_active_store

    return get_active_store(request).store


def user_enters_platform_scope(user) -> bool:
    """
    Whether the authenticated user operates in platform (global) scope.

    Extend here for support/analytics roles without scattering checks.
    """
    return bool(
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
    )
