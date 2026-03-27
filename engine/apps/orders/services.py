from decimal import Decimal

from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from engine.apps.customers.models import Customer
from engine.apps.coupons.services import reverse_coupon_usage_for_order
from django.db import IntegrityError

from engine.apps.orders.models import Order, OrderStatusHistory, StockRestoreLog
from engine.apps.orders.pricing import PricingEngine
from engine.apps.orders.stock import adjust_stock
from engine.apps.products.models import Product, ProductVariant
from engine.apps.stores.models import Store


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


def recalculate_order_totals(order: Order) -> Order:
    """
    Recompute totals from persisted order items to avoid partial updates.
    """
    items = order.items.all()
    pricing_lines: list[dict] = []
    for item in items:
        if not item.product:
            continue
        pricing_lines.append(
            {
                "product": item.product,
                "quantity": int(item.quantity),
                "unit_price": Decimal(str(item.price)),
            }
        )
    breakdown = PricingEngine.compute(
        store=order.store,
        lines=pricing_lines,
        coupon_code=order.coupon_code,
        user=order.user,
        shipping_zone_id=order.shipping_zone_id,
        shipping_method_id=order.shipping_method_id,
    )
    order.subtotal = breakdown.base_subtotal
    order.shipping_cost = breakdown.shipping_cost
    order.shipping_zone = breakdown.shipping_zone
    order.shipping_method = breakdown.shipping_method
    order.shipping_rate = breakdown.shipping_rate
    order.discount_amount = breakdown.bulk_discount_total + breakdown.coupon_discount
    order.coupon = breakdown.coupon
    order.total = breakdown.final_total
    order.save(
        update_fields=[
            "subtotal",
            "shipping_cost",
            "shipping_zone",
            "shipping_method",
            "shipping_rate",
            "discount_amount",
            "coupon",
            "total",
        ]
    )
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


ORDER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    Order.Status.PENDING: {
        Order.Status.CONFIRMED,
        Order.Status.FAILED,
        Order.Status.CANCELLED,
    },
    Order.Status.CONFIRMED: {
        Order.Status.PROCESSING,
        Order.Status.FAILED,
        Order.Status.CANCELLED,
    },
    Order.Status.PROCESSING: {
        Order.Status.SHIPPED,
        Order.Status.FAILED,
        Order.Status.CANCELLED,
    },
    Order.Status.SHIPPED: {
        Order.Status.DELIVERED,
    },
    Order.Status.DELIVERED: set(),
    Order.Status.FAILED: set(),
    Order.Status.CANCELLED: set(),
    # Legacy status kept as terminal to avoid breaking historical data.
    Order.Status.RETURNED: set(),
}


def get_allowed_next_order_statuses(current_status: str) -> list[str]:
    return sorted(ORDER_STATUS_TRANSITIONS.get(current_status, set()))


def ensure_valid_order_status_transition(*, from_status: str, to_status: str) -> None:
    if to_status not in dict(Order.Status.choices):
        raise ValidationError({"status": "Invalid order status."})
    allowed = ORDER_STATUS_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValidationError(
            {
                "status": (
                    f"Invalid status transition from '{from_status}' to '{to_status}'. "
                    f"Allowed: {', '.join(sorted(allowed)) or 'none'}."
                )
            }
        )


def transition_order_status(
    *,
    order: Order,
    to_status: str,
    note: str = "",
    actor_label: str = "",
) -> Order:
    terminal_restore_statuses = {
        Order.Status.FAILED,
        Order.Status.CANCELLED,
        Order.Status.RETURNED,
    }
    with transaction.atomic():
        locked = Order.objects.select_for_update().prefetch_related("items").get(pk=order.pk)
        from_status = locked.status
        ensure_valid_order_status_transition(from_status=from_status, to_status=to_status)
        if from_status == to_status:
            return locked

        if to_status in terminal_restore_statuses:
            for item in locked.items.all():
                try:
                    restore_log, created = StockRestoreLog.objects.get_or_create(
                        order=locked,
                        order_item=item,
                        reason=to_status,
                        defaults={
                            "store_id": locked.store_id,
                            "quantity": int(item.quantity),
                        },
                    )
                except IntegrityError:
                    # Concurrent transition retries can race; uniqueness preserves idempotency.
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
            reverse_coupon_usage_for_order(order=locked, reason=to_status)

        locked.status = to_status
        locked.save(update_fields=["status", "updated_at"])
        note_text = (note or "").strip()
        if actor_label:
            note_text = f"{actor_label}: {note_text}" if note_text else actor_label
        OrderStatusHistory.objects.create(
            order=locked,
            status=to_status,
            note=note_text[:255],
        )
        return locked
