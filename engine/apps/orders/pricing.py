from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from engine.apps.shipping.service import quote_shipping


@dataclass(frozen=True)
class PricingLineBreakdown:
    product_public_id: str
    quantity: int
    unit_price: Decimal
    line_subtotal: Decimal


@dataclass(frozen=True)
class PricingBreakdown:
    base_subtotal: Decimal
    shipping_cost: Decimal
    shipping_zone: object
    shipping_method: object
    shipping_rate: object
    final_total: Decimal
    lines: list[PricingLineBreakdown]


class PricingEngine:
    """Centralized order pricing: merchandise subtotal then shipping."""

    @staticmethod
    def _money(value: Decimal) -> Decimal:
        return Decimal(value).quantize(Decimal("0.01"))

    @classmethod
    def compute(
        cls,
        *,
        store,
        lines: list[dict],
        shipping_zone_pk=None,
        shipping_method_pk=None,
        resolved_shipping_zone=None,
    ) -> PricingBreakdown:
        base_subtotal = Decimal("0.00")
        breakdown_lines: list[PricingLineBreakdown] = []

        for line in lines:
            product = line["product"]
            quantity = int(line["quantity"])
            unit_price = cls._money(line["unit_price"])
            line_subtotal = cls._money(unit_price * quantity)
            base_subtotal += line_subtotal
            breakdown_lines.append(
                PricingLineBreakdown(
                    product_public_id=str(product.public_id),
                    quantity=quantity,
                    unit_price=unit_price,
                    line_subtotal=line_subtotal,
                )
            )

        base_subtotal = cls._money(base_subtotal)
        shipping_quote = quote_shipping(
            store=store,
            order_subtotal=base_subtotal,
            shipping_zone_pk=shipping_zone_pk,
            shipping_method_pk=shipping_method_pk,
            resolved_zone=resolved_shipping_zone,
        )
        shipping_cost = cls._money(shipping_quote.shipping_cost)
        final_total = cls._money(base_subtotal + shipping_cost)
        return PricingBreakdown(
            base_subtotal=base_subtotal,
            shipping_cost=shipping_cost,
            shipping_zone=shipping_quote.zone,
            shipping_method=shipping_quote.method,
            shipping_rate=shipping_quote.rate,
            final_total=final_total,
            lines=breakdown_lines,
        )


def pricing_snapshot_from_breakdown(breakdown: PricingBreakdown) -> dict:
    """JSON-serializable checkout breakdown for persisted orders and storefront APIs."""
    return {
        "base_subtotal": str(breakdown.base_subtotal),
        "shipping_cost": str(breakdown.shipping_cost),
        "final_total": str(breakdown.final_total),
        "lines": [
            {
                "product_public_id": pl.product_public_id,
                "quantity": pl.quantity,
                "unit_price": str(pl.unit_price),
                "line_subtotal": str(pl.line_subtotal),
            }
            for pl in breakdown.lines
        ],
    }


def storefront_pricing_breakdown_response(breakdown: PricingBreakdown) -> dict:
    """Unified storefront pricing JSON (string decimals) for cart breakdown and single-line preview."""
    snap = pricing_snapshot_from_breakdown(breakdown)
    return {
        "base_subtotal": snap["base_subtotal"],
        "shipping_cost": snap["shipping_cost"],
        "final_total": snap["final_total"],
        "lines": snap["lines"],
    }
