"""Store-scoped helpers used by billing, emails, and serializers."""

import hashlib
import re
import hmac
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.text import slugify

from engine.apps.billing.feature_gate import has_feature
from engine.core import cache_service
from engine.core.request_context import get_store_settings_request_cache

from .models import Store, StoreApiKey, StoreMembership, StoreSettings
from .store_activity import touch_store_activity

User = get_user_model()

ORDER_EMAIL_NOTIFICATIONS_FEATURE = "order_email_notifications"


def get_request_store_settings_row(
    request: HttpRequest, store: Store
) -> StoreSettings | None:
    """At most one StoreSettings DB read per store per request (ContextVar cache)."""
    cache = get_store_settings_request_cache()
    if store.pk in cache:
        return cache[store.pk]
    row = StoreSettings.objects.filter(store_id=store.pk).only("storefront_public").first()
    cache[store.pk] = row
    return row


def set_request_store_settings_row(
    request: HttpRequest, store: Store, row: StoreSettings | None
) -> None:
    """Update request-scoped settings cache (e.g. after PATCH)."""
    get_store_settings_request_cache()[store.pk] = row


def normalize_store_code_base_from_name(name: str) -> str:
    """Uppercase alphanumeric segment from store name (max 10) for provisioning Store.code."""
    base = slugify((name or "").strip())[:100]
    return re.sub(r"[^A-Z0-9]", "", base.upper())[:10]


def allocate_unique_store_code(base: str, *, exclude_pk: int | None = None) -> str:
    """
    Allocate a unique Store.code (max 10 chars) from a non-empty normalized base.
    """
    explicit = re.sub(r"[^A-Z0-9]", "", (base or "").strip().upper())[:10]
    if not explicit:
        raise ValueError("Store code base must be non-empty after normalization.")
    candidate = explicit[:10]
    qs = Store.objects.all()
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    n = 2
    while qs.filter(code=candidate).exists():
        suffix = str(n)
        head = max(1, 10 - len(suffix))
        root = explicit[:head].rstrip() or explicit[:1]
        candidate = (root + suffix)[:10]
        n += 1
    return candidate


def _api_key_secret() -> bytes:
    # Prefer a dedicated secret for API key hashing; fall back to SECRET_KEY.
    secret = (
        getattr(settings, "STORE_API_KEY_SECRET", "") or getattr(settings, "SECRET_KEY", "")
    ).strip()
    return secret.encode("utf-8")


def _hash_store_api_key(raw_key: str) -> str:
    material = (raw_key or "").strip().encode("utf-8")
    return hmac.new(_api_key_secret(), material, hashlib.sha256).hexdigest()


def generate_store_api_key(*, key_type: str) -> str:
    """
    Create a high-entropy plaintext API key for one-time display.
    """
    if key_type == StoreApiKey.KeyType.SECRET:
        return f"ak_sk_{secrets.token_urlsafe(24)}"
    return f"ak_pk_{secrets.token_urlsafe(24)}"


def create_store_api_key(
    store: Store, *, name: str = "", key_type: str = StoreApiKey.KeyType.PUBLIC
) -> tuple[StoreApiKey, str]:
    """
    Issue a new API key for the store.
    Returns (row, plaintext_key). The plaintext must not be persisted.
    """
    if key_type not in {StoreApiKey.KeyType.PUBLIC, StoreApiKey.KeyType.SECRET}:
        raise ValueError("Invalid key_type")
    raw = generate_store_api_key(key_type=key_type)
    key_hash = _hash_store_api_key(raw)
    key_name = (name or "").strip()[:80] or "Default key"
    key_prefix = "ak_sk" if key_type == StoreApiKey.KeyType.SECRET else "ak_pk"
    scopes = (
        ["admin:all", "mutations:all"]
        if key_type == StoreApiKey.KeyType.SECRET
        else ["products:read", "orders:create", "orders:read"]
    )
    with transaction.atomic():
        row = StoreApiKey.objects.create(
            store=store,
            key_hash=key_hash,
            key_prefix=key_prefix,
            key_last4=raw[-4:],
            label=key_name,
            key_type=key_type,
            scopes=scopes,
            is_active=True,
        )
    return row, raw


def revoke_store_api_key(key_row: StoreApiKey) -> None:
    if key_row.revoked_at is not None:
        return
    key_row.revoked_at = timezone.now()
    key_row.is_active = False
    key_row.save(update_fields=["revoked_at", "is_active", "updated_at"])


def get_active_store_api_key(store: Store, *, public_id: str | None = None) -> StoreApiKey | None:
    qs = StoreApiKey.objects.filter(store=store, revoked_at__isnull=True, is_active=True)
    if public_id:
        qs = qs.filter(public_id=public_id)
    return (
        qs
        .order_by("-created_at")
        .first()
    )


def resolve_active_store_api_key(raw_key: str) -> StoreApiKey | None:
    if not raw_key:
        return None
    digest = _hash_store_api_key(raw_key)
    return (
        StoreApiKey.objects.select_related("store")
        .filter(
            key_hash=digest,
            revoked_at__isnull=True,
            is_active=True,
            store__is_active=True,
        )
        .first()
    )


def touch_store_api_key_last_used(key_row: StoreApiKey) -> None:
    """
    Best-effort usage timestamp update for monitoring.
    """
    interval_seconds = int(
        getattr(settings, "STORE_API_KEY_LAST_USED_TOUCH_INTERVAL_SECONDS", 60)
    )
    now = timezone.now()
    last_used_at = getattr(key_row, "last_used_at", None)
    if (
        last_used_at is not None
        and interval_seconds > 0
        and (now - last_used_at).total_seconds() < interval_seconds
    ):
        return
    StoreApiKey.objects.filter(pk=key_row.pk).update(last_used_at=now)
    touch_store_activity(key_row.store)


def is_public_api_enabled_for_store(store: Store) -> bool:
    if not store or not store.is_active:
        return False
    return StoreSettings.objects.filter(store=store, public_api_enabled=True).exists()


def get_store_owner_user(store: Store) -> User | None:
    """Owner user for the store (FK), falling back to OWNER membership."""
    oid = getattr(store, "owner_id", None)
    if oid:
        return User.objects.filter(pk=oid).first()
    m = (
        StoreMembership.objects.filter(
            store=store,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        )
        .select_related("user")
        .first()
    )
    return m.user if m else None


def sync_store_owner_to_user(store: Store) -> None:
    """When Store.owner_name changes, mirror it onto the owner User first/last name."""
    owner = get_store_owner_user(store)
    if not owner:
        return
    raw = (store.owner_name or "").strip()
    if not raw:
        return
    parts = raw.split(None, 1)
    first = (parts[0] or "")[:150]
    last = (parts[1] if len(parts) > 1 else "")[:150]
    if owner.first_name == first and owner.last_name == last:
        return
    owner.first_name = first
    owner.last_name = last
    owner.save(update_fields=["first_name", "last_name"])


# ---------------------------------------------------------------------------
# Store settings cache
# ---------------------------------------------------------------------------

def get_cached_store_settings(store_public_id: str):
    """Return cached settings response data, or ``None`` on miss."""
    key = cache_service.build_key(store_public_id, "store_settings", "current")
    return cache_service.get(key)


def set_cached_store_settings(store_public_id: str, data) -> None:
    """Cache settings response data."""
    key = cache_service.build_key(store_public_id, "store_settings", "current")
    cache_service.set(key, data, settings.CACHE_TTL_STORE_SETTINGS)


def invalidate_store_settings_cache(store_public_id: str) -> None:
    """Clear settings cache for a store."""
    cache_service.delete(
        cache_service.build_key(store_public_id, "store_settings", "current")
    )


def sync_order_email_notification_settings_for_user(user) -> None:
    """
    When the user loses premium order-email entitlement, disable both flags
    on the store they own.
    """
    if has_feature(user, ORDER_EMAIL_NOTIFICATIONS_FEATURE):
        return
    owned = getattr(user, "owned_store", None)
    if owned is None:
        return
    StoreSettings.objects.update_or_create(
        store_id=owned.pk,
        defaults={
            "email_notify_owner_on_order_received": False,
            "email_customer_on_order_confirmed": False,
        },
    )
