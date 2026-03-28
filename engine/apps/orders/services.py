from decimal import Decimal

from django.db import IntegrityError
from django.db import transaction
from django.db.models import F
from rest_framework.exceptions import ValidationError

from engine.apps.customers.models import Customer
from engine.apps.orders.models import Order, StockRestoreLog
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
        shipping_zone_pk=order.shipping_zone_id,
        shipping_method_pk=order.shipping_method_id,
    )
    order.subtotal = breakdown.base_subtotal
    order.shipping_cost = breakdown.shipping_cost
    order.shipping_zone = breakdown.shipping_zone
    order.shipping_method = breakdown.shipping_method
    order.shipping_rate = breakdown.shipping_rate
    order.total = breakdown.final_total
    order.save(
        update_fields=[
            "subtotal",
            "shipping_cost",
            "shipping_zone",
            "shipping_method",
            "shipping_rate",
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

        if from_status == Order.Status.CANCELLED and to_status != Order.Status.CANCELLED:
            raise ValidationError(
                {"status": "Cannot change status of a cancelled order."}
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
        return locked
