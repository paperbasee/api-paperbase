"""Transactional emails for store lifecycle (enqueue Celery tasks)."""
from __future__ import annotations

from engine.apps.emails.constants import (
    STORE_DELETE_CANCELLED,
    STORE_DELETE_OTP,
    STORE_DELETE_SCHEDULED,
    STORE_INACTIVE_RECOVERY_REMINDER,
    STORE_PENDING_DELETE_1D,
    STORE_PENDING_DELETE_2D,
    STORE_PERMANENTLY_DELETED,
    STORE_RESTORE_OTP,
    STORE_RESTORED,
    STORE_REMOVED_INACTIVE,
)
from engine.utils.time import format_bd_with_label
from engine.apps.emails.tasks import send_email_task

from .models import Store
from .store_lifecycle import (
    DELETE_OTP_TTL_MINUTES,
    OTP_TTL_MINUTES,
    resolve_owner_email_for_store,
)


def owner_and_contact_emails(store: Store) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in (store.owner_email, store.contact_email):
        e = (raw or "").strip().lower()
        if e and e not in seen:
            seen.add(e)
            out.append((raw or "").strip())
    return out


def owner_email_only(store: Store) -> str | None:
    """Resolve the single destination for lifecycle notifications (owner only)."""
    email = resolve_owner_email_for_store(store)
    return (email or "").strip() or None


def queue_store_removed_inactive(store: Store) -> None:
    ctx = {
        "store_name": store.name,
        "delete_at": format_bd_with_label(store.delete_at) if store.delete_at else "",
        "message": (
            "has been removed and is now inactive. You can recover it within 30 days from the dashboard."
        ),
    }
    owner = owner_email_only(store)
    if owner:
        send_email_task.delay(STORE_REMOVED_INACTIVE, owner, ctx)


def queue_inactive_recovery_reminder(store: Store) -> None:
    ctx = {
        "store_name": store.name,
        "delete_at": format_bd_with_label(store.delete_at) if store.delete_at else "",
        "message": "Restore your store from the dashboard to keep your data.",
    }
    owner = owner_email_only(store)
    if owner:
        send_email_task.delay(STORE_INACTIVE_RECOVERY_REMINDER, owner, ctx)


def queue_delete_scheduled(store: Store, *, from_inactivity: bool = False) -> None:
    ctx = {
        "store_name": store.name,
        "delete_at": format_bd_with_label(store.delete_at) if store.delete_at else "",
        "message": (
            "Your store is queued for permanent deletion. You can still restore it from the dashboard "
            "before the date above."
            if not from_inactivity
            else "Your store had no API activity for 30 days and is queued for deletion. "
            "Restore from the dashboard before the date above."
        ),
    }
    owner = owner_email_only(store)
    if owner:
        send_email_task.delay(STORE_DELETE_SCHEDULED, owner, ctx)


def queue_pending_delete_2d(store: Store) -> None:
    ctx = {
        "store_name": store.name,
        "delete_at": format_bd_with_label(store.delete_at) if store.delete_at else "",
        "message": "Restore your store from the dashboard if you want to keep it.",
    }
    owner = owner_email_only(store)
    if owner:
        send_email_task.delay(STORE_PENDING_DELETE_2D, owner, ctx)


def queue_pending_delete_1d(store: Store) -> None:
    ctx = {
        "store_name": store.name,
        "delete_at": format_bd_with_label(store.delete_at) if store.delete_at else "",
        "message": "This is your last chance to restore from the dashboard.",
    }
    owner = owner_email_only(store)
    if owner:
        send_email_task.delay(STORE_PENDING_DELETE_1D, owner, ctx)


def queue_store_restored_active(store: Store) -> None:
    ctx = {
        "store_name": store.name,
        "message": "Your store has been successfully restored and is active again.",
    }
    owner = owner_email_only(store)
    if owner:
        send_email_task.delay(STORE_RESTORED, owner, ctx)


def queue_store_delete_cancelled(store: Store) -> None:
    ctx = {
        "store_name": store.name,
        "message": (
            "Your store deletion has been cancelled and the store has been restored."
        ),
    }
    owner = owner_email_only(store)
    if owner:
        send_email_task.delay(STORE_DELETE_CANCELLED, owner, ctx)


def queue_store_permanently_deleted(store_name: str, emails: list[str]) -> None:
    ctx = {
        "store_name": store_name,
        "message": "Your store has been permanently deleted.",
    }
    for email in emails:
        if email:
            send_email_task.delay(STORE_PERMANENTLY_DELETED, email.strip(), ctx)


def queue_store_delete_otp_email(
    *,
    store: Store,
    code: str,
    owner_email_fallback: str | None = None,
) -> None:
    """Send OTP to store owner only (for confirming scheduled permanent deletion)."""
    owner = resolve_owner_email_for_store(store, owner_email_fallback)
    if not owner:
        return
    ctx = {
        "store_name": store.name,
        "code": code,
        "minutes": str(DELETE_OTP_TTL_MINUTES),
    }
    send_email_task.delay(STORE_DELETE_OTP, owner, ctx)


def queue_restore_otp_emails(
    *,
    store: Store,
    owner_plain: str,
    contact_plain: str | None,
    single_channel: bool,
    owner_email_fallback: str | None = None,
) -> None:
    owner = resolve_owner_email_for_store(store, owner_email_fallback)
    contact = (store.contact_email or "").strip()
    base = {
        "store_name": store.name,
        "minutes": str(OTP_TTL_MINUTES),
    }
    if single_channel and owner:
        ctx = {
            **base,
            "code": owner_plain,
            "channel_label": "owner and store contact",
        }
        send_email_task.delay(STORE_RESTORE_OTP, owner, ctx)
        return
    if owner:
        send_email_task.delay(
            STORE_RESTORE_OTP,
            owner,
            {**base, "code": owner_plain, "channel_label": "store owner"},
        )
    if contact and contact_plain:
        send_email_task.delay(
            STORE_RESTORE_OTP,
            contact,
            {**base, "code": contact_plain, "channel_label": "store contact email"},
        )

