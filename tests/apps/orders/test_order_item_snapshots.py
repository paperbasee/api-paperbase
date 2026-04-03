from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from engine.apps.inventory.models import Inventory
from engine.apps.orders.models import OrderItem
from engine.apps.products.models import (
    Category,
    Product,
    ProductAttribute,
    ProductAttributeValue,
    ProductVariant,
    ProductVariantAttribute,
)
from engine.apps.shipping.models import ShippingZone
from engine.apps.stores.models import Store, StoreMembership
from engine.apps.stores.services import (
    allocate_unique_store_code,
    create_store_api_key,
    normalize_store_code_base_from_name,
)
from engine.core.tenant_execution import tenant_scope_from_store

User = get_user_model()


def _make_store(name: str) -> Store:
    base = normalize_store_code_base_from_name(name) or "T"
    return Store.objects.create(
        name=name,
        code=allocate_unique_store_code(base),
        owner_name=f"{name} Owner",
        owner_email=f"{name.lower().replace(' ', '')}@example.com",
    )


def _make_product(store: Store, *, name: str = "Product", price: int = 100, stock: int = 20) -> Product:
    with tenant_scope_from_store(store=store, reason="test fixture"):
        category = Category.objects.create(
            store=store,
            name=f"{name} Category",
            slug="",
        )
        product = Product.objects.create(
            store=store,
            category=category,
            name=name,
            price=price,
            stock=stock,
            status=Product.Status.ACTIVE,
            is_active=True,
        )
        Inventory.objects.get_or_create(
            product=product,
            variant=None,
            defaults={"quantity": max(0, int(stock))},
        )
    return product


def _make_zone(store: Store) -> ShippingZone:
    return ShippingZone.objects.create(store=store, name="Main Zone", is_active=True)


def _make_variant_with_options(
    store: Store,
    product: Product,
    *,
    size_value: str = "XL",
    color_value: str = "Red",
) -> ProductVariant:
    with tenant_scope_from_store(store=store, reason="test fixture"):
        size_attr = ProductAttribute.objects.create(store=store, name="Size", slug="size", order=0)
        color_attr = ProductAttribute.objects.create(store=store, name="Color", slug="color", order=1)
        size_av = ProductAttributeValue.objects.create(store=store, attribute=size_attr, value=size_value)
        color_av = ProductAttributeValue.objects.create(store=store, attribute=color_attr, value=color_value)
        variant = ProductVariant.objects.create(product=product, is_active=True)
        ProductVariantAttribute.objects.create(variant=variant, attribute_value=size_av)
        ProductVariantAttribute.objects.create(variant=variant, attribute_value=color_av)
        Inventory.objects.create(product=product, variant=variant, quantity=5)
    return variant


def _api_key_client(api_key: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {api_key}")
    return client


def _admin_client_for_store(store: Store) -> APIClient:
    user = User.objects.create_user(
        email=f"admin-{store.public_id}@example.com",
        password="pass1234",
    )
    user.is_verified = True
    user.is_staff = True
    user.save(update_fields=["is_verified", "is_staff"])
    StoreMembership.objects.create(
        user=user,
        store=store,
        role=StoreMembership.Role.OWNER,
        is_active=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    client.credentials(HTTP_X_STORE_PUBLIC_ID=store.public_id)
    return client


@pytest.mark.django_db
def test_create_order_saves_snapshot_fields():
    store = _make_store("Snapshot Create")
    product = _make_product(store, name="T-Shirt", price=200, stock=10)
    zone = _make_zone(store)
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)

    payload = {
        "shipping_zone_public_id": zone.public_id,
        "shipping_name": "Alice",
        "phone": "01712345678",
        "email": "alice@example.com",
        "shipping_address": "Dhaka",
        "products": [{"product_public_id": product.public_id, "quantity": 2}],
    }
    response = client.post("/api/v1/orders/", payload, format="json")
    assert response.status_code == 201

    with tenant_scope_from_store(store=store, reason="test assertions"):
        item = OrderItem.objects.select_related("product").get(order__public_id=response.data["public_id"])
    assert item.product_name_snapshot == "T-Shirt"
    assert item.variant_snapshot is None
    assert item.unit_price_snapshot == Decimal("200.00")


@pytest.mark.django_db
def test_delete_product_sets_fk_null_and_keeps_snapshots():
    store = _make_store("Snapshot Delete")
    product = _make_product(store, name="Delete Me", price=150, stock=5)
    zone = _make_zone(store)
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)
    payload = {
        "shipping_zone_public_id": zone.public_id,
        "shipping_name": "Alice",
        "phone": "01712345678",
        "email": "alice@example.com",
        "shipping_address": "Dhaka",
        "products": [{"product_public_id": product.public_id, "quantity": 1}],
    }
    response = client.post("/api/v1/orders/", payload, format="json")
    assert response.status_code == 201

    with tenant_scope_from_store(store=store, reason="test assertions"):
        item = OrderItem.objects.get(order__public_id=response.data["public_id"])
        assert item.product_id is not None
        snapshot_name = item.product_name_snapshot
        snapshot_price = item.unit_price_snapshot
        product.delete()
        item.refresh_from_db()
        assert item.product is None
        assert item.product_name_snapshot == snapshot_name
        assert item.unit_price_snapshot == snapshot_price


@pytest.mark.django_db
def test_fetch_order_returns_snapshot_and_null_product():
    store = _make_store("Snapshot Fetch")
    product = _make_product(store, name="Archived Shirt", price=175, stock=5)
    zone = _make_zone(store)
    _key_row, api_key = create_store_api_key(store, name="frontend")
    storefront_client = _api_key_client(api_key)
    payload = {
        "shipping_zone_public_id": zone.public_id,
        "shipping_name": "Alice",
        "phone": "01712345678",
        "email": "alice@example.com",
        "shipping_address": "Dhaka",
        "products": [{"product_public_id": product.public_id, "quantity": 1}],
    }
    create_response = storefront_client.post("/api/v1/orders/", payload, format="json")
    assert create_response.status_code == 201
    order_public_id = create_response.data["public_id"]

    with tenant_scope_from_store(store=store, reason="test assertions"):
        product.delete()

    admin_client = _admin_client_for_store(store)
    response = admin_client.get(f"/api/v1/orders/{order_public_id}/")
    assert response.status_code == 200
    line = response.data["items"][0]
    assert line["product"] is None
    assert line["product_name_snapshot"] == "Archived Shirt"
    assert str(line["unit_price_snapshot"]) == "175.00"


@pytest.mark.django_db
def test_snapshot_fields_are_immutable_after_create():
    store = _make_store("Snapshot Immutable")
    product = _make_product(store, name="Immutable Shirt", price=250, stock=4)
    zone = _make_zone(store)
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)
    payload = {
        "shipping_zone_public_id": zone.public_id,
        "shipping_name": "Alice",
        "phone": "01712345678",
        "email": "alice@example.com",
        "shipping_address": "Dhaka",
        "products": [{"product_public_id": product.public_id, "quantity": 1}],
    }
    response = client.post("/api/v1/orders/", payload, format="json")
    assert response.status_code == 201

    with tenant_scope_from_store(store=store, reason="test assertions"):
        item = OrderItem.objects.get(order__public_id=response.data["public_id"])
        item.product_name_snapshot = "Mutated"
        with pytest.raises(Exception):
            item.save()


@pytest.mark.django_db
def test_cross_tenant_order_detail_does_not_leak_snapshots():
    store_a = _make_store("Tenant A")
    store_b = _make_store("Tenant B")
    product_b = _make_product(store_b, name="Tenant B Shirt", price=300, stock=4)
    zone_b = _make_zone(store_b)
    _key_row, api_key_b = create_store_api_key(store_b, name="frontend-b")
    storefront_client_b = _api_key_client(api_key_b)
    payload = {
        "shipping_zone_public_id": zone_b.public_id,
        "shipping_name": "Bob",
        "phone": "01712345678",
        "email": "bob@example.com",
        "shipping_address": "Dhaka",
        "products": [{"product_public_id": product_b.public_id, "quantity": 1}],
    }
    create_response = storefront_client_b.post("/api/v1/orders/", payload, format="json")
    assert create_response.status_code == 201

    admin_client_a = _admin_client_for_store(store_a)
    response = admin_client_a.get(f"/api/v1/orders/{create_response.data['public_id']}/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_variant_snapshot_is_human_readable_option_labels():
    store = _make_store("Snapshot Variant Labels")
    product = _make_product(store, name="Variant Tee", price=180, stock=0)
    variant = _make_variant_with_options(store, product, size_value="XL", color_value="Red")
    zone = _make_zone(store)
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)

    payload = {
        "shipping_zone_public_id": zone.public_id,
        "shipping_name": "Alice",
        "phone": "01712345678",
        "email": "alice@example.com",
        "shipping_address": "Dhaka",
        "products": [
            {
                "product_public_id": product.public_id,
                "variant_public_id": variant.public_id,
                "quantity": 1,
            }
        ],
    }
    response = client.post("/api/v1/orders/", payload, format="json")
    assert response.status_code == 201

    with tenant_scope_from_store(store=store, reason="test assertions"):
        item = OrderItem.objects.get(order__public_id=response.data["public_id"])
    assert item.variant_snapshot == "Size: XL, Color: Red"


@pytest.mark.django_db
def test_product_name_update_does_not_mutate_existing_orderitem_snapshot():
    store = _make_store("Snapshot Name Freeze")
    product = _make_product(store, name="Legacy Tee", price=120, stock=8)
    zone = _make_zone(store)
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)

    payload = {
        "shipping_zone_public_id": zone.public_id,
        "shipping_name": "Alice",
        "phone": "01712345678",
        "email": "alice@example.com",
        "shipping_address": "Dhaka",
        "products": [{"product_public_id": product.public_id, "quantity": 1}],
    }
    create_response = client.post("/api/v1/orders/", payload, format="json")
    assert create_response.status_code == 201

    with tenant_scope_from_store(store=store, reason="test assertions"):
        old_item = OrderItem.objects.get(order__public_id=create_response.data["public_id"])
        old_name_snapshot = old_item.product_name_snapshot
        old_price_snapshot = old_item.unit_price_snapshot
        product.name = "Renamed Tee"
        product.save(update_fields=["name"])
        old_item.refresh_from_db()
        assert old_item.product_name_snapshot == old_name_snapshot
        assert old_item.unit_price_snapshot == old_price_snapshot


@pytest.mark.django_db
def test_product_price_update_only_affects_new_orders_not_existing_snapshots():
    store = _make_store("Snapshot Price Freeze")
    product = _make_product(store, name="Price Tee", price=110, stock=20)
    zone = _make_zone(store)
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)

    payload = {
        "shipping_zone_public_id": zone.public_id,
        "shipping_name": "Alice",
        "phone": "01712345678",
        "email": "alice@example.com",
        "shipping_address": "Dhaka",
        "products": [{"product_public_id": product.public_id, "quantity": 1}],
    }
    first_response = client.post("/api/v1/orders/", payload, format="json")
    assert first_response.status_code == 201

    with tenant_scope_from_store(store=store, reason="test assertions"):
        first_item = OrderItem.objects.get(order__public_id=first_response.data["public_id"])
        first_snapshot_price = first_item.unit_price_snapshot
        product.price = Decimal("260.00")
        product.save(update_fields=["price"])
        first_item.refresh_from_db()
        assert first_item.unit_price_snapshot == first_snapshot_price

    second_response = client.post("/api/v1/orders/", payload, format="json")
    assert second_response.status_code == 201
    with tenant_scope_from_store(store=store, reason="test assertions"):
        second_item = OrderItem.objects.get(order__public_id=second_response.data["public_id"])
    assert second_item.unit_price_snapshot == Decimal("260.00")
