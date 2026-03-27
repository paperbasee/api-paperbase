from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from engine.apps.coupons.models import Coupon
from engine.apps.coupons.services import CouponValidator, DiscountResolver
from engine.apps.shipping.service import quote_shipping


@dataclass(frozen=True)
class PricingLineBreakdown:
    product_id: str
    quantity: int
    unit_price: Decimal
    line_subtotal: Decimal
    bulk_rule_public_id: Optional[str]
    bulk_discount_amount: Decimal


@dataclass(frozen=True)
class PricingBreakdown:
    base_subtotal: Decimal
    bulk_discount_total: Decimal
    subtotal_after_bulk: Decimal
    coupon_discount: Decimal
    subtotal_after_coupon: Decimal
    shipping_cost: Decimal
    shipping_zone: object
    shipping_method: object
    shipping_rate: object
    final_total: Decimal
    coupon: Optional[Coupon]
    lines: list[PricingLineBreakdown]


class PricingEngine:
    """Centralized order pricing with strict rule order: bulk -> coupon -> shipping."""

    @staticmethod
    def _money(value: Decimal) -> Decimal:
        return Decimal(value).quantize(Decimal("0.01"))

    @classmethod
    def compute(
        cls,
        *,
        store,
        lines: list[dict],
        coupon_code: str = "",
        user=None,
        shipping_zone_id=None,
        shipping_method_id=None,
    ) -> PricingBreakdown:
        base_subtotal = Decimal("0.00")
        bulk_discount_total = Decimal("0.00")
        breakdown_lines: list[PricingLineBreakdown] = []

        for line in lines:
            product = line["product"]
            quantity = int(line["quantity"])
            unit_price = cls._money(line["unit_price"])
            line_subtotal = cls._money(unit_price * quantity)
            base_subtotal += line_subtotal

            bulk_quote = DiscountResolver.resolve_bulk_discount_for_product(
                store=store,
                product=product,
                line_subtotal=line_subtotal,
            )
            bulk_amount = cls._money(bulk_quote.discount_amount)
            bulk_discount_total += bulk_amount
            breakdown_lines.append(
                PricingLineBreakdown(
                    product_id=str(product.public_id),
                    quantity=quantity,
                    unit_price=unit_price,
                    line_subtotal=line_subtotal,
                    bulk_rule_public_id=getattr(bulk_quote.rule, "public_id", None),
                    bulk_discount_amount=bulk_amount,
                )
            )

        base_subtotal = cls._money(base_subtotal)
        bulk_discount_total = cls._money(bulk_discount_total)
        subtotal_after_bulk = cls._money(max(Decimal("0.00"), base_subtotal - bulk_discount_total))

        applied_coupon = None
        coupon_discount = Decimal("0.00")
        normalized_code = (coupon_code or "").strip()
        if normalized_code:
            coupon_quote = CouponValidator.validate_for_subtotal(
                store=store,
                code=normalized_code,
                subtotal=subtotal_after_bulk,
                user=user,
            )
            applied_coupon = coupon_quote.coupon
            coupon_discount = cls._money(coupon_quote.discount_amount)

        subtotal_after_coupon = cls._money(max(Decimal("0.00"), subtotal_after_bulk - coupon_discount))
        shipping_quote = quote_shipping(
            store=store,
            order_subtotal=subtotal_after_coupon,
            shipping_zone_id=shipping_zone_id,
            shipping_method_id=shipping_method_id,
        )
        shipping_cost = cls._money(shipping_quote.shipping_cost)
        final_total = cls._money(subtotal_after_coupon + shipping_cost)
        return PricingBreakdown(
            base_subtotal=base_subtotal,
            bulk_discount_total=bulk_discount_total,
            subtotal_after_bulk=subtotal_after_bulk,
            coupon_discount=coupon_discount,
            subtotal_after_coupon=subtotal_after_coupon,
            shipping_cost=shipping_cost,
            shipping_zone=shipping_quote.zone,
            shipping_method=shipping_quote.method,
            shipping_rate=shipping_quote.rate,
            final_total=final_total,
            coupon=applied_coupon,
            lines=breakdown_lines,
        )
