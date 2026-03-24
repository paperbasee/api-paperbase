from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError

from engine.apps.stores.models import Store

from .models import ShippingMethod, ShippingRate, ShippingZone

@dataclass(frozen=True)
class ShippingQuote:
    shipping_cost: Decimal
    method: ShippingMethod | None = None
    rate: ShippingRate | None = None
    zone: ShippingZone | None = None


def quote_shipping(
    *,
    store: Store,
    order_subtotal: Decimal,
    shipping_zone_id: int | None,
    shipping_method_id: int | None = None,
) -> ShippingQuote:
    """
    Return the best matching shipping quote for a zone-selected order.
    """
    if shipping_zone_id is None:
        raise ValidationError("Shipping zone is required.")

    zone = ShippingZone.objects.filter(
        store=store,
        is_active=True,
        id=shipping_zone_id,
    ).first()
    if zone is None:
        raise ValidationError("Invalid shipping zone for this store.")

    methods = (
        ShippingMethod.objects.filter(store=store, is_active=True)
        .prefetch_related("zones")
        .order_by("order", "id")
    )
    if shipping_method_id is not None:
        methods = methods.filter(id=shipping_method_id)

    best: ShippingQuote | None = None

    for method in methods:
        method_zone_ids = set(method.zones.values_list("id", flat=True))
        if method_zone_ids and zone.id not in method_zone_ids:
            continue

        rates = (
            ShippingRate.objects.filter(
                store=store,
                is_active=True,
                shipping_method=method,
                shipping_zone=zone,
            )
            .select_related("shipping_zone", "shipping_method")
            .order_by("price", "id")
        )
        for rate in rates:
            if rate.min_order_total is not None and order_subtotal < rate.min_order_total:
                continue
            if rate.max_order_total is not None and order_subtotal > rate.max_order_total:
                continue
            quote = ShippingQuote(
                shipping_cost=rate.price,
                method=method,
                rate=rate,
                zone=rate.shipping_zone,
            )
            if best is None:
                best = quote
            else:
                if quote.shipping_cost < best.shipping_cost:
                    best = quote
                elif quote.shipping_cost == best.shipping_cost:
                    if (quote.method.order, quote.method.id) < (best.method.order, best.method.id):  # type: ignore[union-attr]
                        best = quote
            break

    return best or ShippingQuote(shipping_cost=Decimal("0.00"), zone=zone)

