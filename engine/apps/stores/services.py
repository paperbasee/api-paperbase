"""Store-scoped helpers used by billing, emails, and serializers."""

import secrets
import string
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from engine.apps.billing.feature_gate import has_feature

from .models import Domain, Store, StoreMembership, StoreSettings

User = get_user_model()

ORDER_EMAIL_NOTIFICATIONS_FEATURE = "order_email_notifications"

_LABEL_ALPHABET = string.ascii_lowercase + string.digits


def normalize_domain_host(value: str) -> str:
    """Lowercase hostname without port or scheme (for storage and lookups)."""
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        parsed = urlparse(raw)
        raw = parsed.netloc or parsed.path.split("/")[0]
    return raw.split(":", 1)[0].strip().rstrip(".")


def store_primary_domain_host(store: Store) -> str | None:
    """Primary verified hostname for emails and legacy `Store.domain`-style display."""
    d = (
        Domain.objects.filter(store=store, is_primary=True, is_verified=True)
        .values_list("domain", flat=True)
        .first()
    )
    if d:
        return d
    d = (
        Domain.objects.filter(store=store, is_verified=True)
        .order_by("is_custom", "created_at")
        .values_list("domain", flat=True)
        .first()
    )
    return d


def provision_generated_domain(store: Store) -> Domain:
    """
    Create the single generated subdomain for a store: <random 8–12>.PLATFORM_ROOT_DOMAIN.
    Caller must ensure no generated domain exists yet for this store.
    """
    root = getattr(settings, "PLATFORM_ROOT_DOMAIN", "akkho.com").lower().strip(".")
    while True:
        n = secrets.randbelow(5) + 8
        label = "".join(secrets.choice(_LABEL_ALPHABET) for _ in range(n))
        host = f"{label}.{root}"
        with transaction.atomic():
            if Domain.objects.filter(domain=host).exists():
                continue
            has_primary = Domain.objects.filter(store=store, is_primary=True).exists()
            return Domain.objects.create(
                store=store,
                domain=host,
                is_custom=False,
                is_verified=True,
                is_primary=not has_primary,
                verification_token=None,
            )


def ensure_generated_store_domain(store: Store) -> Domain | None:
    """Idempotent: provision generated domain when missing (e.g. new Store)."""
    if Domain.objects.filter(store=store, is_custom=False).exists():
        return None
    return provision_generated_domain(store)


def repromote_generated_domain_primary(store: Store) -> None:
    """After removing a custom domain, ensure the generated hostname is primary again."""
    from engine.core.domain_resolution_cache import invalidate_domain_hosts

    hosts = list(Domain.objects.filter(store=store).values_list("domain", flat=True))
    Domain.objects.filter(store=store, is_custom=False).update(
        is_primary=True,
        updated_at=timezone.now(),
    )
    invalidate_domain_hosts(hosts)


def get_store_owner_user(store: Store) -> User | None:
    """Active OWNER membership user for the store, or None."""
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


def sync_order_email_notification_settings_for_user(user) -> None:
    """
    When the user loses premium order-email entitlement, disable both flags
    on every store they own.
    """
    if has_feature(user, ORDER_EMAIL_NOTIFICATIONS_FEATURE):
        return
    store_ids = StoreMembership.objects.filter(
        user=user,
        role=StoreMembership.Role.OWNER,
        is_active=True,
    ).values_list("store_id", flat=True)
    for store_id in store_ids:
        StoreSettings.objects.update_or_create(
            store_id=store_id,
            defaults={
                "email_notify_owner_on_order_received": False,
                "email_customer_on_order_confirmed": False,
            },
        )
