"""
Redis/LocMem cache for verified domain → tenant resolution.

Cached values never include internal DB ids; only public_ids and flags.
"""

from __future__ import annotations

import json
from typing import Optional, TypedDict

from django.conf import settings
from django.core.cache import caches

from engine.apps.stores.models import Domain, Store


def _tenant_resolution_cache():
    alias = getattr(settings, "TENANT_RESOLUTION_CACHE_ALIAS", "tenant_resolution")
    return caches[alias]


class DomainResolutionPayload(TypedDict):
    domain_public_id: str
    store_public_id: str
    is_verified: bool
    is_primary: bool


def _cache_key(host: str) -> str:
    return f"domain:{host}"


def invalidate_domain_host(host: str) -> None:
    if host:
        _tenant_resolution_cache().delete(_cache_key(host))


def invalidate_domain_hosts(hosts: list[str]) -> None:
    c = _tenant_resolution_cache()
    for h in hosts:
        if h:
            c.delete(_cache_key(h))


def invalidate_domain_cache_for_store(store: Store) -> None:
    hosts = list(
        Domain.objects.filter(store=store).values_list("domain", flat=True)
    )
    invalidate_domain_hosts(hosts)


def _load_from_db(normalized_host: str) -> Optional[DomainResolutionPayload]:
    row = (
        Domain.objects.select_related("store")
        .filter(
            domain=normalized_host,
            is_verified=True,
            store__is_active=True,
        )
        .first()
    )
    if not row:
        return None
    return {
        "domain_public_id": row.public_id,
        "store_public_id": row.store.public_id,
        "is_verified": row.is_verified,
        "is_primary": row.is_primary,
    }


def get_domain_resolution_payload(normalized_host: str) -> Optional[DomainResolutionPayload]:
    """
    Return resolution payload for a verified, active-store domain host, or None.

    Only successful (tenant-resolvable) rows are cached.
    """
    if not normalized_host:
        return None
    c = _tenant_resolution_cache()
    key = _cache_key(normalized_host)
    raw = c.get(key)
    if raw is not None:
        if isinstance(raw, dict) and "store_public_id" in raw:
            return raw  # type: ignore[return-value]
        if isinstance(raw, memoryview):
            raw = raw.tobytes().decode()
        if isinstance(raw, bytes):
            raw = raw.decode()
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and "store_public_id" in data:
                return data  # type: ignore[return-value]
    payload = _load_from_db(normalized_host)
    if payload is not None:
        ttl = int(getattr(settings, "DOMAIN_RESOLUTION_CACHE_TTL", 420))
        c.set(key, json.dumps(payload), ttl)
    return payload


def resolve_store_from_host_cached(normalized_host: str) -> Optional[Store]:
    payload = get_domain_resolution_payload(normalized_host)
    if not payload:
        return None
    store = Store.objects.filter(
        public_id=payload["store_public_id"],
        is_active=True,
    ).first()
    if store is not None:
        return store
    # Cached payload can become stale between test DB resets or store rotations.
    invalidate_domain_host(normalized_host)
    payload = _load_from_db(normalized_host)
    if payload is None:
        return None
    ttl = int(getattr(settings, "DOMAIN_RESOLUTION_CACHE_TTL", 420))
    _tenant_resolution_cache().set(_cache_key(normalized_host), json.dumps(payload), ttl)
    return Store.objects.filter(
        public_id=payload["store_public_id"],
        is_active=True,
    ).first()


def resolve_store_public_id_from_host_cached(normalized_host: str) -> Optional[str]:
    payload = get_domain_resolution_payload(normalized_host)
    if not payload:
        return None
    if Store.objects.filter(public_id=payload["store_public_id"], is_active=True).exists():
        return payload["store_public_id"]
    invalidate_domain_host(normalized_host)
    payload = _load_from_db(normalized_host)
    if payload is None:
        return None
    ttl = int(getattr(settings, "DOMAIN_RESOLUTION_CACHE_TTL", 420))
    _tenant_resolution_cache().set(_cache_key(normalized_host), json.dumps(payload), ttl)
    return payload["store_public_id"]
