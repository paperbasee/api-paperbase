"""Central pricing + billing-cycle math for subscriptions.

Rules:
- Plan.price is the *monthly-equivalent display price* for both cycles.
- Monthly charge = price * 1
- Yearly charge = price * 12 (paid upfront)
"""

from decimal import Decimal, ROUND_HALF_UP

from .models import Plan


TWOPLACES = Decimal("0.01")


def quantize_money(amount: Decimal) -> Decimal:
    return amount.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def billing_cycle_duration_days(billing_cycle: str) -> int:
    if billing_cycle == Plan.BillingCycle.MONTHLY:
        return 30
    if billing_cycle == Plan.BillingCycle.YEARLY:
        return 365
    raise ValueError(f"Unsupported billing_cycle: {billing_cycle!r}")


def plan_charge_amount(plan: Plan) -> Decimal:
    """Amount user must pay now for this plan."""
    price = Decimal(str(plan.price))
    if plan.billing_cycle == Plan.BillingCycle.MONTHLY:
        return quantize_money(price)
    if plan.billing_cycle == Plan.BillingCycle.YEARLY:
        return quantize_money(price * Decimal("12"))
    raise ValueError(f"Unsupported plan.billing_cycle: {plan.billing_cycle!r}")


def plan_monthly_equivalent_price(plan: Plan) -> Decimal:
    """Display price (monthly-equivalent) for UI."""
    return quantize_money(Decimal(str(plan.price)))

