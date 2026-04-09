"""Billing service layer. All subscription state changes MUST go through these functions."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from engine.utils.time import bd_today, format_bd_date
from engine.apps.emails.triggers import (
    queue_platform_new_subscription_email,
    queue_subscription_activated_email,
    queue_subscription_changed_email,
    queue_subscription_payment_email,
    subscription_payment_receipt_worth_sending,
)
from engine.apps.stores.services import sync_order_email_notification_settings_for_user

from .models import Payment, Plan, Subscription


def get_active_subscription(user):
    """
    Return the single active subscription for the user, or None.

    Active = status='active' and end_date >= today.
    """
    today = bd_today()
    return (
        Subscription.objects.filter(
            user=user,
            status=Subscription.Status.ACTIVE,
            end_date__gte=today,
        )
        .select_related("plan")
        .first()
    )


@transaction.atomic
def activate_subscription(
    user,
    plan,
    billing_cycle="monthly",
    duration_days=30,
    source="manual",
    amount=0,
    provider="manual",
    change_reason: str = "",
    existing_pending_payment: Payment | None = None,
):
    """
    Activate a new subscription for the user. Expires any current active subscription.

    Args:
        user: User to activate subscription for
        plan: Plan instance
        billing_cycle: 'monthly' or 'yearly'
        duration_days: Number of days for the subscription
        source: 'payment', 'manual', or 'trial'
        amount: Payment amount (Decimal or int/float)
        provider: Payment provider (e.g. 'manual', 'bkash', 'stripe')
        change_reason: Optional note for SUBSCRIPTION_CHANGED (e.g. admin action label)
        existing_pending_payment: If set, this row is linked to the new subscription and
            marked SUCCESS instead of creating a second Payment (manual checkout approval).

    Returns:
        The new Subscription instance.
    """
    today = bd_today()
    end_date = today + timedelta(days=duration_days)

    prev_sub = (
        Subscription.objects.filter(
            user=user,
            status=Subscription.Status.ACTIVE,
        )
        .select_related("plan")
        .order_by("-created_at")
        .first()
    )
    prev_plan = prev_sub.plan if prev_sub else None

    # Expire current active subscription(s)
    Subscription.objects.filter(
        user=user,
        status=Subscription.Status.ACTIVE,
    ).update(status=Subscription.Status.EXPIRED, updated_at=timezone.now())

    # Create new subscription
    subscription = Subscription.objects.create(
        user=user,
        plan=plan,
        status=Subscription.Status.ACTIVE,
        billing_cycle=billing_cycle,
        start_date=today,
        end_date=end_date,
        auto_renew=False,
        source=source,
    )

    # Payment row: reuse manual checkout pending row, or create a new success record
    amount_decimal = Decimal(str(amount)) if amount is not None else Decimal("0")
    if existing_pending_payment is not None:
        ep = existing_pending_payment
        if ep.user_id != user.id:
            raise ValueError("existing_pending_payment must belong to the same user.")
        if ep.status != Payment.Status.PENDING:
            raise ValueError("existing_pending_payment must be in PENDING status.")
        if ep.plan_id != plan.id:
            raise ValueError("existing_pending_payment plan must match the given plan.")
        ep.subscription = subscription
        ep.status = Payment.Status.SUCCESS
        ep.save(update_fields=["subscription", "status"])
        payment = ep
    else:
        payment = Payment.objects.create(
            user=user,
            plan=plan,
            subscription=subscription,
            amount=amount_decimal,
            currency="BDT",
            status=Payment.Status.SUCCESS,
            provider=provider,
            transaction_id=None,
            metadata={},
        )

    payment_receipt = subscription_payment_receipt_worth_sending(
        subscription.source, payment.amount, payment.provider
    )
    if payment_receipt:
        queue_subscription_payment_email(user, subscription, payment)

    plan_changed = prev_plan is not None and prev_plan.id != plan.id
    if plan_changed:
        queue_subscription_changed_email(
            user=user,
            subscription=subscription,
            old_plan_name=prev_plan.name,
            new_plan_name=subscription.plan.name,
            effective_date=format_bd_date(subscription.start_date),
            change_reason=change_reason,
        )
    else:
        queue_subscription_activated_email(
            user,
            subscription,
            payment,
            payment_receipt_sent_separately=payment_receipt,
        )

    if prev_plan is None:
        queue_platform_new_subscription_email(user, subscription)

    sync_order_email_notification_settings_for_user(user)

    from .feature_gate import invalidate_feature_config_cache
    invalidate_feature_config_cache(user)

    return subscription


@transaction.atomic
def extend_subscription(subscription, days):
    """
    Extend the end_date of a subscription by the given number of days.

    Raises ValueError if the subscription is not active (canceled/expired
    subscriptions must not be resurrected via extension).

    Args:
        subscription: Subscription instance to extend
        days: Number of days to add

    Returns:
        The updated Subscription instance.
    """
    if subscription.status != Subscription.Status.ACTIVE:
        raise ValueError(
            f"Cannot extend a {subscription.status} subscription. "
            "Only active subscriptions can be extended."
        )

    today = bd_today()
    current_end = subscription.end_date
    new_end = max(current_end, today) + timedelta(days=days)

    subscription.end_date = new_end
    subscription.save(update_fields=["end_date", "updated_at"])

    from .feature_gate import invalidate_feature_config_cache
    invalidate_feature_config_cache(subscription.user)

    return subscription
