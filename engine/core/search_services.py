from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from engine.apps.customers.models import Customer
from engine.apps.orders.models import Order
from engine.apps.products.models import Product
from engine.apps.stores.models import Store
from engine.apps.support.models import SupportTicket

DEFAULT_PER_TYPE_LIMIT = 10
MAX_PER_TYPE_LIMIT = 20


@dataclass(frozen=True)
class SearchResultItem:
    public_id: str
    title: str
    subtitle: str = ""


def _normalize_limit(per_type_limit: int | None) -> int:
    if per_type_limit is None:
        return DEFAULT_PER_TYPE_LIMIT
    return max(1, min(int(per_type_limit), MAX_PER_TYPE_LIMIT))


def _product_supports_is_deleted() -> bool:
    return any(field.name == "is_deleted" for field in Product._meta.fields)


def _search_products(query: str, store: Store, limit: int) -> list[SearchResultItem]:
    filters = (
        Q(name__icontains=query)
        | Q(sku__icontains=query)
        | Q(description__icontains=query)
    )

    qs = Product.objects.select_related("category").filter(
        store=store,
        is_active=True,
    )
    if _product_supports_is_deleted():
        qs = qs.filter(is_deleted=False)

    products = (
        qs.filter(filters)
        .only("public_id", "name", "sku", "category__name")
        .order_by("-created_at")[:limit]
    )
    return [
        SearchResultItem(
            public_id=product.public_id,
            title=product.name,
            subtitle=product.sku or "",
        )
        for product in products
    ]


def _search_orders(query: str, store: Store, limit: int) -> list[SearchResultItem]:
    filters = (
        Q(order_number__icontains=query)
        | Q(public_id__icontains=query)
        | Q(customer__name__icontains=query)
        | Q(phone__icontains=query)
    )
    orders = (
        Order.objects.select_related("customer")
        .filter(store=store)
        .filter(filters)
        .only("public_id", "order_number", "phone", "customer__name")
        .order_by("-created_at")[:limit]
    )
    return [
        SearchResultItem(
            public_id=order.public_id,
            title=order.order_number or order.public_id,
            subtitle=order.customer.name if order.customer and order.customer.name else order.phone,
        )
        for order in orders
    ]


def _search_customers(query: str, store: Store, limit: int) -> list[SearchResultItem]:
    filters = (
        Q(name__icontains=query)
        | Q(phone__icontains=query)
        | Q(email__icontains=query)
    )
    customers = (
        Customer.objects.filter(store=store)
        .filter(filters)
        .only("public_id", "name", "phone", "email")
        .order_by("-created_at")[:limit]
    )
    return [
        SearchResultItem(
            public_id=customer.public_id,
            title=customer.name or customer.phone or customer.email or customer.public_id,
            subtitle=customer.phone or customer.email or "",
        )
        for customer in customers
    ]


def _search_tickets(query: str, store: Store, limit: int) -> list[SearchResultItem]:
    filters = (
        Q(subject__icontains=query)
        | Q(public_id__icontains=query)
        | Q(name__icontains=query)
        | Q(email__icontains=query)
        | Q(phone__icontains=query)
    )
    tickets = (
        SupportTicket.objects.filter(store=store)
        .filter(filters)
        .only("public_id", "subject", "name", "email", "phone")
        .order_by("-created_at")[:limit]
    )
    return [
        SearchResultItem(
            public_id=ticket.public_id,
            title=ticket.subject or ticket.public_id,
            subtitle=ticket.name or ticket.email or ticket.phone or "",
        )
        for ticket in tickets
    ]


def search(query: str, store: Store, per_type_limit: int | None = None) -> dict[str, list[dict[str, str]]]:
    normalized_query = (query or "").strip()
    if not normalized_query or not store:
        return {"products": [], "orders": [], "customers": [], "tickets": []}

    limit = _normalize_limit(per_type_limit)
    products = _search_products(normalized_query, store, limit)
    orders = _search_orders(normalized_query, store, limit)
    customers = _search_customers(normalized_query, store, limit)
    tickets = _search_tickets(normalized_query, store, limit)

    return {
        "products": [item.__dict__ for item in products],
        "orders": [item.__dict__ for item in orders],
        "customers": [item.__dict__ for item in customers],
        "tickets": [item.__dict__ for item in tickets],
    }
