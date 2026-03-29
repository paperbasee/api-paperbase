"""
Structured order text for transactional emails (ORDER_RECEIVED / ORDER_CONFIRMED).

Uses stored Order totals only (no pricing recomputation).
District resolution is shared with Steadfast address formatting.
"""

from __future__ import annotations

from engine.apps.orders.models import Order, OrderItem

DISTRICT_NOT_SPECIFIED = "Not specified"


def resolve_district(order: Order) -> str:
    """
    Prefer Order.district; else best-effort parse from shipping_address (e.g. trailing
    comma segment common in BD addresses). Not guaranteed accurate.
    """
    raw = (getattr(order, "district", None) or "").strip()
    if raw:
        return raw
    addr = (getattr(order, "shipping_address", None) or "").strip()
    if not addr:
        return DISTRICT_NOT_SPECIFIED
    parts = [p.strip() for p in addr.split(",") if p.strip()]
    if len(parts) >= 2:
        return parts[-1]
    return DISTRICT_NOT_SPECIFIED


def _item_display_name(item: OrderItem) -> str:
    base = item.product.name if item.product else "Unavailable"
    if item.variant_id and item.variant:
        sku = (item.variant.sku or "").strip()
        if sku:
            return f"{base} ({sku})"
    return base


def format_item_lines(order: Order) -> list[str]:
    """Human-readable bullet lines: '- Name xqty'."""
    qs = order.items.select_related("product", "variant").order_by("id")
    return [f"- {_item_display_name(item)} x{item.quantity}" for item in qs]


def build_structured_order_summary(order: Order) -> str:
    """
    Single multiline block: order meta, address, district, items, delivery, total, currency.
    """
    store = order.store
    currency = (getattr(store, "currency", None) or "").strip() or "—"
    sym = (getattr(store, "currency_symbol", None) or "").strip()
    currency_suffix = f" {sym} {currency}".strip() if sym else f" {currency}"

    lines = [
        f"Order: #{order.order_number}",
        f"Customer: {(order.shipping_name or '').strip() or '—'}",
        f"Phone: {(order.phone or '').strip() or '—'}",
        f"Address: {(order.shipping_address or '').strip() or '—'}",
        f"District: {resolve_district(order)}",
        "",
        "Items:",
    ]
    item_lines = format_item_lines(order)
    if item_lines:
        lines.extend(item_lines)
    else:
        lines.append("- (no line items)")
    lines.extend(
        [
            "",
            f"Delivery charge: {order.shipping_cost}{currency_suffix}",
            f"Total: {order.total}{currency_suffix}",
        ]
    )
    return "\n".join(lines)


def build_order_email_context(order: Order) -> dict:
    """Context keys for ORDER_RECEIVED / ORDER_CONFIRMED; merge with existing dicts."""
    store = order.store
    summary = build_structured_order_summary(order)
    return {
        "order_summary": summary,
        "shipping_address": (order.shipping_address or "").strip(),
        "district": resolve_district(order),
        "phone": (order.phone or "").strip(),
        "delivery_charge": str(order.shipping_cost),
        "total": str(order.total),
        "currency": getattr(store, "currency", "") or "",
        "currency_symbol": (getattr(store, "currency_symbol", None) or "").strip(),
        "items_lines": format_item_lines(order),
    }
