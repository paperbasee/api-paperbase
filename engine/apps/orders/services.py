from decimal import Decimal

from django.db import IntegrityError
from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from engine.apps.customers.models import Customer
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

        if order.customer_id != customer.pk:
            order.customer = customer
            order.save(update_fields=["customer"])

        Customer.objects.filter(pk=customer.pk, store=store).update(total_orders=F("total_orders") + 1)
        customer.refresh_from_db(fields=["total_orders"])
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

        locked.status = to_status
        locked.save(update_fields=["status", "updated_at"])
        invalidate_notifications_and_dashboard_caches(locked.store.public_id)
        return locked
