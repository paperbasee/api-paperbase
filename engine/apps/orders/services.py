from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.db.models import Max, Min
from django.utils import timezone
from rest_framework.exceptions import ValidationError

import logging

from engine.apps.customers.models import Customer

logger = logging.getLogger(__name__)
from engine.apps.orders.models import Order, OrderItem, StockRestoreLog
from engine.apps.orders.order_financials import (
    compute_line_financials,
    money,
    aggregate_order_item_snapshots,
    build_pricing_snapshot_dict,
    quote_shipping_for_order,
)
from engine.apps.orders.stock import adjust_stock
from engine.apps.products.models import Product, ProductVariant
from engine.apps.stores.models import Store
from engine.core.admin_dashboard_cache import invalidate_notifications_and_dashboard_caches


def _normalize_phone(phone: str) -> str:
    raw = (phone or "").strip()
    digits = "".join(c for c in raw if c.isdigit())
    return digits


def resolve_and_attach_customer(
    order: Order,
    *,
    store: Store,
    name: str,
    phone: str,
    email: str | None,
    address: str | None,
) -> Customer:
    """
    Resolve per-store customer by phone and attach it to the order.
    Identity is strictly (store, phone); email is not used for matching.
    """
    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        raise ValueError("phone is required")

    normalized_name = (name or "").strip()
    normalized_email = (email or "").strip() or None
    normalized_address = (address or "").strip() or None

    with transaction.atomic():
        customer, _ = Customer.objects.select_for_update().get_or_create(
            store=store,
            phone=normalized_phone,
            defaults={
                "name": normalized_name,
                "email": normalized_email,
                "address": normalized_address,
            },
        )

        update_fields: list[str] = []
        if normalized_name and not (customer.name or "").strip():
            customer.name = normalized_name
            update_fields.append("name")
        if normalized_email and not (customer.email or "").strip():
            customer.email = normalized_email
            update_fields.append("email")
        if normalized_address and not (customer.address or "").strip():
            customer.address = normalized_address
            update_fields.append("address")
        if update_fields:
            customer.save(update_fields=update_fields)

        had_customer = order.customer_id is not None
        if order.customer_id != customer.pk:
            order.customer = customer
            order.save(update_fields=["customer"])

        return customer


def resolve_active_store_product(*, store: Store, product_public_id: str) -> Product:
    """
    Resolve a product scoped to store that can be ordered.
    """
    try:
        return Product.objects.get(
            public_id=product_public_id,
            store=store,
            is_active=True,
            status=Product.Status.ACTIVE,
        )
    except Product.DoesNotExist as exc:
        raise ValueError("Selected product is unavailable.") from exc


def resolve_active_variant_for_product(
    *,
    store: Store,
    product: Product,
    variant_public_id: str | None,
) -> ProductVariant | None:
    """Resolve variant by public_id and catalog scope; SKU is never used as a lookup key."""
    if variant_public_id is None:
        return None
    try:
        return ProductVariant.objects.select_related("product").get(
            public_id=variant_public_id,
            product_id=product.pk,
            product__store=store,
            product__is_active=True,
            product__status=Product.Status.ACTIVE,
            is_active=True,
        )
    except ProductVariant.DoesNotExist as exc:
        raise ValueError(f"Variant {variant_public_id} is unavailable.") from exc


def write_order_item_financials(
    order_item: OrderItem,
    *,
    product,
    variant,
    quantity: int,
    unit_price: Decimal,
) -> None:
    """Populate immutable snapshot fields from catalog + chosen unit price."""
    fin = compute_line_financials(
        product=product,
        variant=variant,
        quantity=quantity,
        unit_price=unit_price,
    )
    order_item.original_price = fin["original_price"]
    order_item.unit_price = fin["unit_price"]
    order_item.discount_amount = fin["discount_amount"]
    order_item.line_subtotal = fin["line_subtotal"]
    order_item.line_total = fin["line_total"]


def build_variant_snapshot_text(variant: ProductVariant | None) -> str | None:
    """
    Human-readable immutable variant label for order snapshots.
    Preference:
    1) ordered attribute labels ("Size: XL, Color: Red")
    2) variant SKU
    3) variant public_id
    """
    if variant is None:
        return None
    links = (
        variant.attribute_values.select_related("attribute_value__attribute")
        .order_by("attribute_value__attribute__order", "attribute_value__order")
        .all()
    )
    labels = [
        f"{link.attribute_value.attribute.name}: {link.attribute_value.value}"
        for link in links
    ]
    if labels:
        return ", ".join(labels)
    return getattr(variant, "sku", None) or variant.public_id


def recalculate_order_totals(order: Order) -> Order:
    """
    Single source: roll up persisted line snapshots only (no Product reads).
    """
    # Always read lines from the DB. `order.items.all()` reuses prefetched caches
    # from the same request (e.g. admin PATCH after updating items), so rollups
    # would use stale snapshots until a later request — matching "save twice" bugs.
    items = list(
        OrderItem.objects.filter(order_id=order.pk).select_related("product", "variant")
    )
    sb, dt, sa = aggregate_order_item_snapshots(items)
    quote = quote_shipping_for_order(
        store=order.store,
        subtotal_after_discount=sa,
        shipping_zone_pk=order.shipping_zone_id,
        shipping_method_pk=order.shipping_method_id,
    )
    ship = money(quote.shipping_cost)
    tot = money(sa + ship)
    line_rows = []
    for oi in items:
        line_rows.append(
            {
                "product_public_id": oi.product.public_id if oi.product else "",
                "quantity": oi.quantity,
                "unit_price": str(oi.unit_price),
                "original_price": str(oi.original_price),
                "discount_amount": str(oi.discount_amount),
                "line_subtotal": str(oi.line_subtotal),
                "line_total": str(oi.line_total),
            }
        )
    order.subtotal_before_discount = sb
    order.discount_total = dt
    order.subtotal_after_discount = sa
    order.shipping_cost = ship
    order.shipping_zone = quote.zone
    order.shipping_method = quote.method
    order.shipping_rate = quote.rate
    order.total = tot
    order.pricing_snapshot = build_pricing_snapshot_dict(
        subtotal_before_discount=sb,
        discount_total=dt,
        subtotal_after_discount=sa,
        shipping_cost=ship,
        total=tot,
        lines=line_rows,
    )
    order.save(
        update_fields=[
            "subtotal_before_discount",
            "discount_total",
            "subtotal_after_discount",
            "shipping_cost",
            "shipping_zone",
            "shipping_method",
            "shipping_rate",
            "total",
            "pricing_snapshot",
        ]
    )
    prefetched = getattr(order, "_prefetched_objects_cache", None)
    if isinstance(prefetched, dict):
        prefetched.pop("items", None)
    return order


def restore_order_item_stock(*, store_id: int, product_id, variant_id, quantity: int) -> None:
    """
    Restore stock for an order item removal.
    """
    adjust_stock(
        store_id=store_id,
        product_id=product_id,
        variant_id=variant_id,
        delta_qty=-int(quantity),
    )


def apply_order_status_change(
    *,
    order: Order,
    to_status: str,
    request=None,
) -> Order:
    """
    Set order status to pending, confirmed, or cancelled.

    Stock is restored (once per line item, idempotent) only when entering cancelled
    from a non-cancelled state.

    Cancelled orders cannot be moved to another status without manual inventory fixes.
    """
    valid = {Order.Status.PENDING, Order.Status.CONFIRMED, Order.Status.CANCELLED}
    if to_status not in valid:
        raise ValidationError({"status": "Invalid order status."})

    with transaction.atomic():
        locked = Order.objects.select_for_update().prefetch_related("items").get(pk=order.pk)
        from_status = locked.status
        has_unavailable_products = any(item.product_id is None for item in locked.items.all())

        if from_status == Order.Status.CANCELLED and to_status != Order.Status.CANCELLED:
            raise ValidationError(
                {"status": "Cannot change status of a cancelled order."}
            )

        if has_unavailable_products:
            raise ValidationError(
                {"status": "Remove unavailable products before updating order status"}
            )

        if from_status == to_status:
            return locked

        if to_status == Order.Status.CANCELLED and from_status != Order.Status.CANCELLED:
            reason = StockRestoreLog.Reason.CANCELLED
            for item in locked.items.all():
                try:
                    restore_log, created = StockRestoreLog.objects.get_or_create(
                        order=locked,
                        order_item=item,
                        reason=reason,
                        defaults={
                            "store_id": locked.store_id,
                            "quantity": int(item.quantity),
                        },
                    )
                except IntegrityError:
                    continue
                if not created:
                    continue
                restore_order_item_stock(
                    store_id=locked.store_id,
                    product_id=item.product_id,
                    variant_id=item.variant_id,
                    quantity=item.quantity,
                )
                if restore_log.quantity != int(item.quantity):
                    restore_log.quantity = int(item.quantity)
                    restore_log.save(update_fields=["quantity"])

        # Customer aggregate rollups are updated *only* on status transitions to keep the
        # customer row lightweight, avoid duplication, and ensure idempotency.
        if locked.customer_id:
            customer = (
                Customer.objects.select_for_update()
                .filter(pk=locked.customer_id, store_id=locked.store_id)
                .first()
            )
            if customer:
                now = timezone.now()

                def _recompute_derived_fields() -> None:
                    customer.is_repeat_customer = bool((customer.total_orders or 0) > 1)
                    if (customer.total_orders or 0) <= 1:
                        customer.avg_order_interval_days = None
                        return
                    if not customer.first_order_at or not customer.last_order_at:
                        customer.avg_order_interval_days = None
                        return
                    span = customer.last_order_at - customer.first_order_at
                    days = Decimal(str(span.total_seconds())) / Decimal("86400")
                    denom = Decimal(int(customer.total_orders) - 1)
                    customer.avg_order_interval_days = (days / denom).quantize(
                        Decimal("0.01"),
                        rounding=ROUND_HALF_UP,
                    )

                # pending/other -> confirmed: increment once
                if to_status == Order.Status.CONFIRMED and from_status != Order.Status.CONFIRMED:
                    items_total = locked.subtotal_after_discount
                    customer.total_orders = int(customer.total_orders or 0) + 1
                    customer.total_spent = (customer.total_spent or Decimal("0.00")) + items_total
                    customer.last_order_at = now
                    if customer.first_order_at is None:
                        customer.first_order_at = now
                    _recompute_derived_fields()
                    customer.save(
                        update_fields=[
                            "total_orders",
                            "total_spent",
                            "first_order_at",
                            "last_order_at",
                            "is_repeat_customer",
                            "avg_order_interval_days",
                            "updated_at",
                        ]
                    )
                # confirmed -> cancelled: rollback once (bounded at 0) and recompute timestamps
                elif from_status == Order.Status.CONFIRMED and to_status == Order.Status.CANCELLED:
                    items_total = locked.subtotal_after_discount
                    customer.total_orders = max(int(customer.total_orders or 0) - 1, 0)
                    customer.total_spent = max(
                        (customer.total_spent or Decimal("0.00")) - items_total,
                        Decimal("0.00"),
                    )

                    # Recompute first/last from remaining CONFIRMED orders only.
                    # We exclude the current order because it is being cancelled.
                    agg = (
                        Order.objects.filter(
                            store_id=locked.store_id,
                            customer_id=locked.customer_id,
                            status=Order.Status.CONFIRMED,
                        )
                        .exclude(pk=locked.pk)
                        .aggregate(first=Min("updated_at"), last=Max("updated_at"))
                    )
                    customer.first_order_at = agg["first"]
                    customer.last_order_at = agg["last"]

                    _recompute_derived_fields()
                    customer.save(
                        update_fields=[
                            "total_orders",
                            "total_spent",
                            "first_order_at",
                            "last_order_at",
                            "is_repeat_customer",
                            "avg_order_interval_days",
                            "updated_at",
                        ]
                    )

        locked.status = to_status
        locked.save(update_fields=["status", "updated_at"])
        invalidate_notifications_and_dashboard_caches(locked.store.public_id)

        # Meta standard event: Purchase only on confirmed transition.
        if (
            request is not None
            and to_status == Order.Status.CONFIRMED
            and from_status != Order.Status.CONFIRMED
        ):
            try:
                from engine.apps.marketing_integrations.tracking import meta_conversions

                purchase_event_id = f"purchase_{locked.public_id}"
                order_for_meta = (
                    Order.objects.select_related("store")
                    .prefetch_related("items__product")
                    .get(pk=locked.pk)
                )
                logger.info(
                    "Meta CAPI Purchase dispatch: order=%s status=%s event_id=%s store=%s",
                    order_for_meta.public_id,
                    order_for_meta.status,
                    purchase_event_id,
                    getattr(order_for_meta.store, "public_id", "—"),
                )
                meta_conversions.track_purchase(request, order_for_meta, event_id=purchase_event_id)
            except Exception:
                # Tracking must never break the business flow.
                logger.exception(
                    "Meta Purchase tracking failed for order %s",
                    getattr(locked, "public_id", locked.pk),
                )

        return locked
