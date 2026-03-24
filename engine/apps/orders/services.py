from decimal import Decimal

from django.db import transaction
from django.db.models import F

from engine.apps.customers.models import Customer
from engine.apps.orders.models import Order
from engine.apps.orders.stock import adjust_stock
from engine.apps.products.models import Product, ProductVariant
from engine.apps.shipping.service import quote_shipping
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
    subtotal = Decimal("0.00")
    items = order.items.all()
    for item in items:
        subtotal += Decimal(str(item.price)) * Decimal(item.quantity)

    quote = quote_shipping(
        store=order.store,
        order_subtotal=subtotal,
        shipping_zone_id=order.shipping_zone_id,
        shipping_method_id=order.shipping_method_id,
    )
    order.subtotal = subtotal
    order.shipping_cost = quote.shipping_cost
    order.shipping_zone = quote.zone
    order.shipping_method = quote.method
    order.shipping_rate = quote.rate
    order.total = subtotal + quote.shipping_cost
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


def restore_order_item_stock(*, product_id, variant_id, quantity: int) -> None:
    """
    Restore stock for an order item removal.
    """
    adjust_stock(product_id=product_id, variant_id=variant_id, delta_qty=-int(quantity))
