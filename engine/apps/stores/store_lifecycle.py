"""
Store lifecycle transitions: remove (inactive), scheduled delete, restore (OTP), inactivity.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.cache import caches
from django.db import transaction
from django.utils import timezone

from engine.apps.stores.models import Store, StoreDeletionOtpChallenge, StoreRestoreChallenge

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser

INACTIVE_RETENTION_DAYS = 30
PENDING_DELETE_GRACE_DAYS = 7
INACTIVITY_DAYS = 30
OTP_TTL_MINUTES = 15
OTP_SEND_COOLDOWN_SECONDS = 60

DELETE_OTP_TTL_MINUTES = 7
DELETE_OTP_SEND_COOLDOWN_SECONDS = 60
DELETE_OTP_CHANNEL = "delete_schedule"

_VALID_TRANSITIONS: dict[str, set[str]] = {
    Store.Status.ACTIVE: {Store.Status.INACTIVE, Store.Status.PENDING_DELETE},
    Store.Status.INACTIVE: {Store.Status.ACTIVE, Store.Status.PENDING_DELETE},
    Store.Status.PENDING_DELETE: {Store.Status.ACTIVE},
}


def transition_store_status(store: Store, new_status: str) -> None:
    """Enforce strict state machine and bump lifecycle_version atomically."""
    allowed = _VALID_TRANSITIONS.get(store.status, set())
    if new_status not in allowed:
        raise ValueError(f"Invalid transition: {store.status} -> {new_status}")
    store.status = new_status
    store.lifecycle_version = (store.lifecycle_version or 0) + 1


def _otp_digest(store_id: int, channel: str, code: str) -> str:
    raw = f"{settings.SECRET_KEY}:{store_id}:{channel}:{code}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_otp_code() -> str:
    return f"{secrets.randbelow(900000) + 100000:06d}"


def touch_store_activity(store: Store) -> None:
    """Update last_activity_at for ACTIVE stores (throttled)."""
    if store.status != Store.Status.ACTIVE:
        return
    interval = int(getattr(settings, "STORE_ACTIVITY_TOUCH_INTERVAL_SECONDS", 60))
    now = timezone.now()
    last = store.last_activity_at
    if last is not None and interval > 0 and (now - last).total_seconds() < interval:
        return
    Store.objects.filter(pk=store.pk, status=Store.Status.ACTIVE).update(last_activity_at=now)


def remove_store(*, store: Store, user: AbstractUser) -> Store:
    """Soft-disable: INACTIVE, removed_at, delete_at = now + 30d."""
    if store.status != Store.Status.ACTIVE:
        raise ValueError("Store is not active.")
    contact = (store.contact_email or "").strip()
    if not contact:
        raise ValueError("Configure a store contact email before removing the store.")
    now = timezone.now()
    transition_store_status(store, Store.Status.INACTIVE)
    store.removed_at = now
    store.delete_at = now + timedelta(days=INACTIVE_RETENTION_DAYS)
    store.inactive_recovery_reminder_sent_at = None
    store.save()
    return store


def schedule_permanent_delete(*, store: Store, user: AbstractUser) -> Store:
    """Queue hard delete in 7 days (from ACTIVE or INACTIVE)."""
    if store.status not in (Store.Status.ACTIVE, Store.Status.INACTIVE):
        raise ValueError("Store cannot be scheduled for deletion.")
    now = timezone.now()
    transition_store_status(store, Store.Status.PENDING_DELETE)
    store.delete_requested_at = now
    store.delete_at = now + timedelta(days=PENDING_DELETE_GRACE_DAYS)
    store.pending_delete_2d_reminder_sent_at = None
    store.pending_delete_1d_reminder_sent_at = None
    store.save()
    return store


def restore_store_after_otp(*, store: Store) -> Store:
    """Clear lifecycle fields and return to ACTIVE."""
    transition_store_status(store, Store.Status.ACTIVE)
    store.removed_at = None
    store.delete_requested_at = None
    store.delete_at = None
    store.inactive_recovery_reminder_sent_at = None
    store.pending_delete_2d_reminder_sent_at = None
    store.pending_delete_1d_reminder_sent_at = None
    store.last_activity_at = timezone.now()
    store.save()
    return store


def apply_inactivity_pending_delete(store: Store) -> Store:
    """Mark ACTIVE store as PENDING_DELETE due to inactivity (30d + 7d window)."""
    if store.status != Store.Status.ACTIVE:
        return store
    now = timezone.now()
    transition_store_status(store, Store.Status.PENDING_DELETE)
    store.delete_requested_at = now
    store.delete_at = now + timedelta(days=PENDING_DELETE_GRACE_DAYS)
    store.pending_delete_2d_reminder_sent_at = None
    store.pending_delete_1d_reminder_sent_at = None
    store.save()
    return store


def resolve_owner_email_for_store(store: Store, fallback: str | None = None) -> str:
    """
    Prefer Store.owner_email; if blank, use fallback (typically the account email of the
    logged-in owner) so restore OTP can always be delivered.
    """
    o = (store.owner_email or "").strip()
    if o:
        return o
    if fallback:
        return fallback.strip()
    return ""


def _rate_limit_delete_otp_send(store_public_id: str, client_key: str) -> bool:
    """Return True if rate limited (too soon) for delete-schedule OTP sends."""
    cache = caches[getattr(settings, "STORE_OTP_RATE_LIMIT_CACHE_ALIAS", "default")]
    key = f"store_delete_otp_send:{store_public_id}:{client_key}"
    if cache.get(key):
        return True
    cache.set(key, "1", DELETE_OTP_SEND_COOLDOWN_SECONDS)
    return False


def _rate_limit_send(store_public_id: str, client_key: str) -> bool:
    """Return True if rate limited (too soon)."""
    cache = caches[getattr(settings, "STORE_OTP_RATE_LIMIT_CACHE_ALIAS", "default")]
    key = f"store_otp_send:{store_public_id}:{client_key}"
    if cache.get(key):
        return True
    cache.set(key, "1", OTP_SEND_COOLDOWN_SECONDS)
    return False


def create_restore_challenge(
    *,
    store: Store,
    purpose: str,
    client_key: str,
    owner_email_fallback: str | None = None,
) -> tuple[StoreRestoreChallenge, str | None, str | None]:
    """
    Create challenge; return (challenge, owner_plain, contact_plain) for sending emails.
    Plain codes are None if single_channel (same code for both — return one string twice in caller).
    """
    if purpose == StoreRestoreChallenge.Purpose.RESTORE_INACTIVE:
        if store.status != Store.Status.INACTIVE:
            raise ValueError("Store is not inactive.")
        if not store.removed_at:
            raise ValueError("Invalid store state.")
        if timezone.now() - store.removed_at >= timedelta(days=INACTIVE_RETENTION_DAYS):
            raise ValueError("Restore window has expired.")
    elif purpose == StoreRestoreChallenge.Purpose.RESTORE_PENDING_DELETE:
        if store.status != Store.Status.PENDING_DELETE:
            raise ValueError("Store is not pending deletion.")
        if not store.delete_at or store.delete_at <= timezone.now():
            raise ValueError("Restore window has expired.")
    else:
        raise ValueError("Invalid purpose.")

    if _rate_limit_send(store.public_id, client_key):
        raise ValueError("Please wait before requesting another code.")

    owner_raw = resolve_owner_email_for_store(store, owner_email_fallback)
    owner_email = owner_raw.lower()
    contact_email = (store.contact_email or "").strip().lower()
    single = owner_email == contact_email and bool(owner_email)

    owner_code = _generate_otp_code()
    if single:
        contact_code = owner_code
        owner_hash = _otp_digest(store.id, "owner", owner_code)
        contact_hash = owner_hash
    else:
        contact_code = _generate_otp_code()
        owner_hash = _otp_digest(store.id, "owner", owner_code)
        contact_hash = _otp_digest(store.id, "contact", contact_code)

    now = timezone.now()
    expires = now + timedelta(minutes=OTP_TTL_MINUTES)

    with transaction.atomic():
        StoreRestoreChallenge.objects.filter(
            store=store,
            purpose=purpose,
            expires_at__gt=now,
        ).delete()
        ch = StoreRestoreChallenge.objects.create(
            store=store,
            purpose=purpose,
            owner_code_hash=owner_hash,
            contact_code_hash=contact_hash,
            single_channel=single,
            expires_at=expires,
        )

    if single:
        return ch, owner_code, None
    return ch, owner_code, contact_code


def verify_restore_challenge_step(
    *,
    challenge: StoreRestoreChallenge,
    owner_code: str | None,
    contact_code: str | None,
) -> StoreRestoreChallenge:
    """Verify one or both codes; updates verified_at fields. Raises ValueError on failure."""
    now = timezone.now()
    if challenge.expires_at <= now:
        raise ValueError("Verification code has expired.")

    store = challenge.store

    def _check(expected_hash: str, plain: str | None, channel: str) -> bool:
        if plain is None or len(plain.strip()) != 6:
            return False
        return secrets.compare_digest(
            expected_hash,
            _otp_digest(store.id, channel, plain.strip()),
        )

    if challenge.single_channel:
        code = (owner_code or contact_code or "").strip()
        if not _check(challenge.owner_code_hash, code, "owner"):
            raise ValueError("Invalid verification code.")
        challenge.owner_verified_at = now
        challenge.contact_verified_at = now
    else:
        oc = (owner_code or "").strip()
        cc = (contact_code or "").strip()
        if oc and cc and challenge.owner_verified_at is None:
            if not _check(challenge.owner_code_hash, oc, "owner"):
                raise ValueError("Invalid owner verification code.")
            if not _check(challenge.contact_code_hash, cc, "contact"):
                raise ValueError("Invalid store contact verification code.")
            challenge.owner_verified_at = now
            challenge.contact_verified_at = now
        elif challenge.owner_verified_at is None:
            if not _check(challenge.owner_code_hash, owner_code, "owner"):
                raise ValueError("Invalid owner verification code.")
            challenge.owner_verified_at = now
        elif challenge.contact_verified_at is None:
            if not _check(challenge.contact_code_hash, contact_code, "contact"):
                raise ValueError("Invalid store contact verification code.")
            challenge.contact_verified_at = now

    challenge.save(
        update_fields=["owner_verified_at", "contact_verified_at"],
    )
    return challenge


def is_restore_challenge_complete(challenge: StoreRestoreChallenge) -> bool:
    if challenge.single_channel:
        return challenge.owner_verified_at is not None
    return challenge.owner_verified_at is not None and challenge.contact_verified_at is not None


def create_deletion_schedule_otp_challenge(
    *,
    store: Store,
    user: AbstractUser,
    client_key: str,
) -> tuple[StoreDeletionOtpChallenge, str]:
    """
    Create a single-use OTP for confirming permanent deletion scheduling.
    Returns (challenge, plaintext_code) for sending to the store owner email only.
    """
    if _rate_limit_delete_otp_send(store.public_id, client_key):
        raise ValueError("Please wait before requesting another code.")

    plain = _generate_otp_code()
    digest = _otp_digest(store.id, DELETE_OTP_CHANNEL, plain)
    now = timezone.now()
    expires = now + timedelta(minutes=DELETE_OTP_TTL_MINUTES)

    with transaction.atomic():
        StoreDeletionOtpChallenge.objects.filter(
            store=store,
            user=user,
            expires_at__gt=now,
        ).delete()
        ch = StoreDeletionOtpChallenge.objects.create(
            store=store,
            user=user,
            code_hash=digest,
            expires_at=expires,
        )
    return ch, plain


def verify_deletion_schedule_otp(*, challenge: StoreDeletionOtpChallenge, code: str) -> None:
    now = timezone.now()
    if challenge.expires_at <= now:
        raise ValueError("Verification code has expired.")
    store = challenge.store
    c = (code or "").strip()
    if len(c) != 6:
        raise ValueError("Invalid verification code.")
    if not secrets.compare_digest(
        challenge.code_hash,
        _otp_digest(store.id, DELETE_OTP_CHANNEL, c),
    ):
        raise ValueError("Invalid verification code.")
