"""Queue transactional emails via Celery. Template bodies live in EmailTemplate rows, not here."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from engine.apps.billing.feature_gate import has_feature
from engine.apps.billing.models import Subscription
from engine.apps.stores.models import StoreSettings
from engine.apps.stores.services import (
    ORDER_EMAIL_NOTIFICATIONS_FEATURE,
    get_store_owner_user,
)

from .constants import (
    GENERIC_NOTIFICATION,
    ORDER_CONFIRMED,
    ORDER_RECEIVED,
    PLATFORM_NEW_SUBSCRIPTION,
    SUBSCRIPTION_ACTIVATED,
    SUBSCRIPTION_CHANGED,
    SUBSCRIPTION_PAYMENT,
    TWO_FA_DISABLE,
)
from .tasks import send_email_task


def _store_internal_email(store) -> str | None:
    """Store-facing inbox: contact_email first, then owner_email."""
    contact = (store.contact_email or "").strip()
    if contact:
        return contact
    owner = (store.owner_email or "").strip()
    return owner or None


def notify_store_new_order(order) -> None:
    """
    ORDER_RECEIVED to store internal email (checkout or manual create).
    Only when the store owner has premium order-email entitlement and the setting is on.
    """
    store = order.store
    owner = get_store_owner_user(store)
    if not owner or not has_feature(owner, ORDER_EMAIL_NOTIFICATIONS_FEATURE):
        return
    settings, _ = StoreSettings.objects.get_or_create(store=store)
    if not settings.email_notify_owner_on_order_received:
        return
    to_email = _store_internal_email(store)
    if not to_email:
        return
    owner_email = (store.owner_email or "").strip()
    send_email_task.delay(
        ORDER_RECEIVED,
        to_email,
        {
            "store_name": store.name,
            "order_number": order.order_number,
            "customer_email": (order.email or "").strip(),
            "customer_name": (order.shipping_name or "").strip(),
            "total": str(order.total),
            "currency": store.currency,
            "store_contact_email": (store.contact_email or "").strip() or owner_email,
        },
    )


def should_send_customer_confirmation_order_email(order) -> bool:
    """Whether send-to-courier should queue ORDER_CONFIRMED (premium + per-store setting)."""
    if order.customer_confirmation_sent_at is not None:
        return False
    owner = get_store_owner_user(order.store)
    if not owner or not has_feature(owner, ORDER_EMAIL_NOTIFICATIONS_FEATURE):
        return False
    settings, _ = StoreSettings.objects.get_or_create(store=order.store)
    return bool(settings.email_customer_on_order_confirmed)


def notify_customer_order_confirmation_send_to_courier(order) -> bool:
    """
    Queue ORDER_CONFIRMED to the customer when enabled.
    Returns True if queued; False if skipped (not enabled, no email) or already sent.
    """
    if order.customer_confirmation_sent_at is not None:
        return False
    if not should_send_customer_confirmation_order_email(order):
        return False
    customer_email = (order.email or "").strip()
    if not customer_email:
        return False
    store = order.store
    send_email_task.delay(
        ORDER_CONFIRMED,
        customer_email,
        {
            "store_name": store.name,
            "order_number": order.order_number,
            "customer_name": (order.shipping_name or "").strip(),
            "total": str(order.total),
            "currency": store.currency,
        },
    )
    return True


def subscription_payment_receipt_worth_sending(source: str, amount, provider: str) -> bool:
    """
    Send receipt-style mail when payment flow or non-zero charge, or non-manual provider.
    Skip typical free admin grants: manual source + manual provider + $0.
    """
    amount_decimal = Decimal(str(amount)) if amount is not None else Decimal("0")
    return (
        source == Subscription.Source.PAYMENT
        or amount_decimal > 0
        or provider not in ("manual",)
    )


def _platform_notification_recipients() -> list[str]:
    from django.conf import settings

    emails = getattr(settings, "PLATFORM_NOTIFICATION_EMAILS", None) or []
    if emails:
        return list(emails)
    from django.contrib.auth import get_user_model

    User = get_user_model()
    return list(
        User.objects.filter(is_superuser=True, is_active=True)
        .values_list("email", flat=True)
        .distinct()
    )


def _primary_owned_store(user):
    from engine.apps.stores.models import StoreMembership

    m = (
        StoreMembership.objects.filter(
            user=user,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        )
        .select_related("store")
        .order_by("-created_at")
        .first()
    )
    return m.store if m else None


def _primary_owned_store_name(user) -> str:
    s = _primary_owned_store(user)
    return s.name if s else "—"


def queue_subscription_payment_email(user, subscription, payment) -> None:
    if not subscription_payment_receipt_worth_sending(
        subscription.source, payment.amount, payment.provider
    ):
        return
    send_email_task.delay(
        SUBSCRIPTION_PAYMENT,
        user.email,
        {
            "user_name": user.get_short_name() or user.email,
            "plan_name": subscription.plan.name,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "billing_date": subscription.end_date.isoformat(),
            "payment_date": timezone.localdate().isoformat(),
        },
    )


def queue_subscription_activated_email(
    user,
    subscription,
    payment,
    *,
    payment_receipt_sent_separately: bool,
) -> None:
    send_email_task.delay(
        SUBSCRIPTION_ACTIVATED,
        user.email,
        {
            "user_name": user.get_short_name() or user.email,
            "plan_name": subscription.plan.name,
            "billing_cycle": subscription.get_billing_cycle_display(),
            "start_date": subscription.start_date.isoformat(),
            "end_date": subscription.end_date.isoformat(),
            "subscription_status": subscription.get_status_display(),
            "amount": str(payment.amount),
            "currency": payment.currency,
            "payment_date": timezone.localdate().isoformat(),
            "payment_receipt_sent_separately": payment_receipt_sent_separately,
        },
    )


def queue_subscription_changed_email(
    user,
    subscription,
    *,
    old_plan_name: str,
    new_plan_name: str,
    effective_date: str,
    change_reason: str | None,
) -> None:
    send_email_task.delay(
        SUBSCRIPTION_CHANGED,
        user.email,
        {
            "user_name": user.get_short_name() or user.email,
            "old_plan_name": old_plan_name,
            "new_plan_name": new_plan_name,
            "effective_date": effective_date,
            "change_reason": (change_reason or "").strip() or "—",
            "plan_name": subscription.plan.name,
            "end_date": subscription.end_date.isoformat(),
            "subscription_status": subscription.get_status_display(),
        },
    )


def queue_platform_new_subscription_email(user, subscription) -> None:
    store = _primary_owned_store(user)
    store_name = store.name if store else "—"
    ctx = {
        "store_owner_email": user.email,
        "store_name": store_name,
        "store_public_id": store.public_id if store else "",
        "store_domain": "",
        "store_owner_name_on_record": (store.owner_name or "") if store else "",
        "store_owner_email_on_record": (store.owner_email or "") if store else "",
        "store_phone": (store.phone or "") if store else "",
        "store_contact_email": (store.contact_email or "") if store else "",
        "store_address": (store.address or "") if store else "",
        "user_public_id": str(user.public_id),
        "user_full_name": user.get_full_name() or user.email,
        "plan_name": subscription.plan.name,
        "subscription_status": subscription.get_status_display(),
        "subscription_source": subscription.get_source_display(),
        "timestamp": timezone.now().isoformat(),
    }
    for to in _platform_notification_recipients():
        if not to:
            continue
        send_email_task.delay(PLATFORM_NEW_SUBSCRIPTION, to, ctx)


def queue_two_fa_disabled_email(user) -> None:
    send_email_task.delay(
        TWO_FA_DISABLE,
        user.email,
        {
            "user_name": user.get_short_name() or user.email,
            "user_email": user.email,
            "disabled_at": timezone.now().isoformat(),
        },
    )


def queue_generic_notification(
    to_email: str,
    *,
    title: str,
    body: str,
    action_url: str | None = None,
) -> None:
    ctx: dict = {"title": title, "body": body}
    if action_url:
        ctx["action_url"] = action_url
    send_email_task.delay(
        GENERIC_NOTIFICATION,
        to_email,
        ctx,
        getattr(settings, "SUPPORT_FROM_EMAIL", "support@akkho.com"),
    )
