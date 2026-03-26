from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.http import HttpRequest

from engine.apps.stores.models import Store, StoreMembership


@dataclass
class ActiveStoreContext:
    store: Optional[Store]
    membership: Optional[StoreMembership]


def get_active_store(request: HttpRequest) -> ActiveStoreContext:
    """
    Resolve the active store for the current request.

    Priority:
    1) API key-resolved request.store (for tenant/public APIs)
    2) Explicit X-Store-Public-ID header (dashboard/admin)
    3) JWT claim `active_store_public_id`
    """
    store: Optional[Store] = getattr(request, "store", None)
    membership: Optional[StoreMembership] = None
    header_store_public_id = request.headers.get("X-Store-Public-ID") or request.headers.get("x-store-public-id")
    token_store_public_id = None
    if getattr(request, "auth", None):
        token_store_public_id = request.auth.get("active_store_public_id")  # type: ignore[union-attr]

    if store is None and header_store_public_id:
        store = Store.objects.filter(public_id=header_store_public_id, is_active=True).first()
    if store is None and token_store_public_id:
        store = Store.objects.filter(public_id=token_store_public_id, is_active=True).first()

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
    DRF storefront views: require API-key resolved store context.
    """
    from rest_framework.exceptions import AuthenticationFailed

    if getattr(request, "store", None) is None:
        raise AuthenticationFailed(detail="Store context missing.")


def require_api_key_store(request: HttpRequest) -> Store:
    """
    Return request.store for storefront flows; fail closed if absent.
    """
    from rest_framework.exceptions import AuthenticationFailed

    store = getattr(request, "store", None)
    if store is None:
        raise AuthenticationFailed(detail="Store context missing.")
    return store
