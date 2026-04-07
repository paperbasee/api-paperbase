from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.http import HttpRequest

from engine.apps.stores.models import Store, StoreMembership


@dataclass
class ActiveStoreContext:
    store: Optional[Store]
    membership: Optional[StoreMembership]


def _membership_for(user, store: Store) -> Optional[StoreMembership]:
    if not user or not getattr(user, "is_authenticated", False):
        return None
    try:
        return StoreMembership.objects.get(
            user=user,
            store=store,
            is_active=True,
        )
    except StoreMembership.DoesNotExist:
        return None


def get_active_store(request: HttpRequest) -> ActiveStoreContext:
    """
    Resolve the active store for the current request.

    Priority:
    1) API key-resolved request.store (storefront / tenant public APIs)
    2) Superuser: optional X-Store-Public-ID for platform tooling
    3) Authenticated store owner: request.user.owned_store (ignores client store hints)
    4) Authenticated staff/non-owner: header store hint (X-Store-ID / X-Store-Public-ID)
    5) Staff (no owned store): JWT claim active_store_public_id only (no header)
    """
    store_from_api_key = getattr(request, "store", None)
    store: Optional[Store] = store_from_api_key
    membership: Optional[StoreMembership] = None
    user = getattr(request, "user", None)

    # 1) Storefront API key
    if store is not None:
        if user and getattr(user, "is_authenticated", False):
            membership = _membership_for(user, store)
        return ActiveStoreContext(store=store, membership=membership)

    # 2) Platform superuser
    if getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False):
        header_store_public_id = request.headers.get("X-Store-Public-ID") or request.headers.get(
            "x-store-public-id"
        )
        if header_store_public_id:
            store = Store.objects.filter(public_id=header_store_public_id).first()
        if store:
            membership = _membership_for(user, store)
        return ActiveStoreContext(store=store, membership=membership)

    # 3) Owner: single source of truth — never trust header/JWT for tenancy
    if getattr(user, "is_authenticated", False):
        owned = getattr(user, "owned_store", None)
        if owned is not None:
            store = owned
            membership = _membership_for(user, store)
            return ActiveStoreContext(store=store, membership=membership)

    # 4) Authenticated staff/non-owner: allow explicit store selection via header.
    #
    # This is needed because tenant context is resolved in middleware (before DRF auth
    # attaches request.auth), so JWT-claim-only selection would otherwise be unavailable.
    if getattr(user, "is_authenticated", False):
        header_store_public_id = (
            request.headers.get("X-Store-ID")
            or request.headers.get("x-store-id")
            or request.headers.get("X-Store-Public-ID")
            or request.headers.get("x-store-public-id")
        )
        if header_store_public_id:
            candidate = Store.objects.filter(public_id=header_store_public_id).first()
            if candidate:
                membership = _membership_for(user, candidate)
                if membership is not None:
                    return ActiveStoreContext(store=candidate, membership=membership)

    # 5) Staff: JWT claim only
    if getattr(user, "is_authenticated", False) and getattr(request, "auth", None):
        token_store_public_id = request.auth.get("active_store_public_id")  # type: ignore[union-attr]
        if token_store_public_id:
            store = Store.objects.filter(public_id=token_store_public_id).first()
        if store:
            membership = _membership_for(user, store)
        if membership is None:
            store = None
        return ActiveStoreContext(store=store, membership=membership)

    return ActiveStoreContext(store=None, membership=None)


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
