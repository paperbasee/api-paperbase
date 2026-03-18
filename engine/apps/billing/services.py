"""Billing service layer. All subscription state changes MUST go through these functions."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import Payment, Plan, Subscription


def get_active_subscription(user):
    """
    Return the single active subscription for the user, or None.

    Active = status='active' and end_date >= today.
    """
    today = timezone.localdate()
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

    Returns:
        The new Subscription instance.
    """
    today = timezone.localdate()
    end_date = today + timedelta(days=duration_days)

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

    # Create payment record
    amount_decimal = Decimal(str(amount)) if amount is not None else Decimal("0")
    Payment.objects.create(
        user=user,
        subscription=subscription,
        amount=amount_decimal,
        currency="BDT",
        status=Payment.Status.SUCCESS,
        provider=provider,
        transaction_id=None,
        metadata={},
    )

    return subscription


@transaction.atomic
def extend_subscription(subscription, days):
    """
    Extend the end_date of a subscription by the given number of days.

    Only extends if the subscription is still active (status=active, end_date >= today).
    If already expired, extends from today.

    Args:
        subscription: Subscription instance to extend
        days: Number of days to add

    Returns:
        The updated Subscription instance.
    """
    today = timezone.localdate()
    current_end = subscription.end_date

    # If expired, extend from today; otherwise from current end_date
    if subscription.status != Subscription.Status.ACTIVE or current_end < today:
        new_end = today + timedelta(days=days)
    else:
        new_end = current_end + timedelta(days=days)

    subscription.end_date = new_end
    subscription.save(update_fields=["end_date", "updated_at"])
    return subscription
