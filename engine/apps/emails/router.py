"""Centralized sender routing by email type."""

from __future__ import annotations

from .constants import (
    EMAIL_VERIFICATION,
    GENERIC_NOTIFICATION,
    ORDER_CONFIRMED,
    ORDER_RECEIVED,
    PASSWORD_RESET,
    PLATFORM_NEW_SUBSCRIPTION,
    SUBSCRIPTION_ACTIVATED,
    SUBSCRIPTION_CHANGED,
    SUBSCRIPTION_PAYMENT,
    TWO_FA_DISABLE,
    TWO_FA_RECOVERY,
)

DEFAULT_SENDER = "noreply@mail.paperbase.me"

# Shown in inbox “From” columns (e.g. “Anthropic” vs raw address) — RFC 5322 display name.
SENDER_DISPLAY_NAME = "Paperbase"

EMAIL_SENDER_MAP: dict[str, str] = {
    PASSWORD_RESET: "security@mail.paperbase.me",
    EMAIL_VERIFICATION: "security@mail.paperbase.me",
    TWO_FA_RECOVERY: "security@mail.paperbase.me",
    TWO_FA_DISABLE: "security@mail.paperbase.me",
    SUBSCRIPTION_PAYMENT: "billing@mail.paperbase.me",
    SUBSCRIPTION_ACTIVATED: "billing@mail.paperbase.me",
    SUBSCRIPTION_CHANGED: "billing@mail.paperbase.me",
    PLATFORM_NEW_SUBSCRIPTION: "billing@mail.paperbase.me",
    ORDER_CONFIRMED: "noreply@mail.paperbase.me",
    ORDER_RECEIVED: "noreply@mail.paperbase.me",
    GENERIC_NOTIFICATION: "noreply@mail.paperbase.me",
}


def format_from_with_display_name(email_address: str) -> str:
    """
    Build a From header value so clients show SENDER_DISPLAY_NAME instead of the bare address.

    If *email_address* already looks like ``Name <addr>``, it is returned unchanged.
    """
    raw = (email_address or "").strip()
    if not raw:
        raw = DEFAULT_SENDER
    if "<" in raw:
        _, after_lt = raw.split("<", 1)
        if ">" in after_lt:
            return raw
    return f"{SENDER_DISPLAY_NAME} <{raw}>"


def resolve_email_sender(email_type: str) -> str:
    """
    Return sender identity for a template type.

    Always returns a Paperbase sender and never an empty value. Includes a display name
    so inbox UIs show “Paperbase” rather than the raw mailbox address.
    """
    normalized = (email_type or "").strip()
    sender = EMAIL_SENDER_MAP.get(normalized, DEFAULT_SENDER)
    if not sender:
        sender = DEFAULT_SENDER
    return format_from_with_display_name(sender)
