from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from engine.apps.stores.models import Store

from .models import ShippingMethod, ShippingRate, ShippingZone


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]


def _csv_contains(csv_value: str | None, needle: str | None) -> bool:
    """
    Match a normalized needle against a comma-separated list.
    Blank csv_value means "no restriction" (matches all).
    """
    if not needle:
        return True
    parts = [p.lower() for p in _split_csv(csv_value)]
    if not parts:
        return True
    return needle.lower() in parts


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
    delivery_area: str | None = None,
    district: str | None = None,
    preferred_method_id: int | None = None,
    preferred_zone_id: int | None = None,
) -> ShippingQuote:
    """
    Return the best matching shipping quote for an order.

    Matching rules (v1):
    - Zones can restrict by `country_codes`, `delivery_areas`, and/or `districts` (all optional).
    - Methods can restrict to specific zones via M2M (empty = all zones in store).
    - Rates match by zone + optional min/max order total.
    - The selected quote is the cheapest active matching rate (ties: method order).
    """
    zones_qs = ShippingZone.objects.filter(store=store, is_active=True)
    zones = list(zones_qs)

    def zone_matches(z: ShippingZone) -> bool:
        if delivery_area and not _csv_contains(z.delivery_areas, delivery_area.lower()):
            return False
        if district and not _csv_contains(z.districts, district):
            return False
        return True

    matched_zone_ids = {z.id for z in zones if zone_matches(z)}
    if preferred_zone_id is not None:
        if preferred_zone_id in matched_zone_ids:
            matched_zone_ids = {preferred_zone_id}
        else:
            # If user explicitly chose a zone, allow it even if it doesn't match the
            # current destination filters (dashboard use-case).
            matched_zone_ids = {preferred_zone_id}

    methods = (
        ShippingMethod.objects.filter(store=store, is_active=True)
        .prefetch_related("zones")
        .order_by("order", "id")
    )
    if preferred_method_id is not None:
        methods = methods.filter(id=preferred_method_id)

    best: ShippingQuote | None = None

    for method in methods:
        method_zone_ids = set(method.zones.values_list("id", flat=True))
        effective_zone_ids = matched_zone_ids
        if method_zone_ids:
            effective_zone_ids = effective_zone_ids & method_zone_ids

        if not effective_zone_ids and matched_zone_ids:
            continue

        rates = (
            ShippingRate.objects.filter(
                store=store,
                is_active=True,
                shipping_method=method,
            )
            .select_related("shipping_zone", "shipping_method")
            .order_by("price", "id")
        )
        for rate in rates:
            if matched_zone_ids and rate.shipping_zone_id not in effective_zone_ids:
                continue
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

    return best or ShippingQuote(shipping_cost=Decimal("0.00"))

