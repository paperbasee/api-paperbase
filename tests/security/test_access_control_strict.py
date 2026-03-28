import pytest
from django.contrib.auth import get_user_model
from django.urls import path
from rest_framework.test import APIClient
from rest_framework.views import APIView

from engine.apps.orders.models import Order, OrderItem, StockRestoreLog
from engine.apps.orders.services import transition_order_status
from engine.apps.inventory.models import Inventory
from engine.apps.products.models import Category, Product, ProductVariant
from engine.apps.products.stock_sync import sync_product_stock_from_variants
from engine.apps.shipping.models import ShippingZone
from engine.apps.stores.models import Store, StoreMembership
from engine.apps.stores.services import create_store_api_key, revoke_store_api_key
from engine.core.tenant_execution import tenant_scope_from_store
from engine.core import store_api_key_auth
from engine.core.apps import enforce_production_override_safety
from engine.core.store_api_key_auth import (
    STORE_FRONTEND_ROUTE_POLICY,
    validate_storefront_api_key_view_flags,
    maybe_validate_storefront_api_key_view_flags,
)
from config.permissions import IsStorefrontAPIKey

User = get_user_model()


@pytest.fixture(autouse=True)
def _enable_tenant_api_key_enforcement(settings):
    settings.TENANT_API_KEY_ENFORCE = True


def _make_store(name: str) -> Store:
    return Store.objects.create(
        name=name,
        owner_name=f"{name} Owner",
        owner_email=f"{name.lower().replace(' ', '')}@example.com",
    )


def _make_product(store: Store, *, name: str = "Product", price: int = 100, stock: int = 20) -> Product:
    with tenant_scope_from_store(store=store, reason="test fixture"):
        category = Category.objects.create(
            store=store,
            name=f"{name} Category",
            slug=f"{name.lower().replace(' ', '-')}-cat",
        )
        p = Product.objects.create(
            store=store,
            category=category,
            name=name,
            price=price,
            stock=stock,
            status=Product.Status.ACTIVE,
            is_active=True,
        )
        Inventory.objects.get_or_create(
            product=p,
            variant=None,
            defaults={"quantity": max(0, int(stock))},
        )
    return p


def _make_zone(store: Store) -> ShippingZone:
    return ShippingZone.objects.create(store=store, name="Main Zone", is_active=True)


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
@pytest.mark.parametrize("path", ["/api/v1/products/", "/api/v1/categories/"])
def test_storefront_api_key_allows_catalog_reads(path):
    store = _make_store("Catalog")
    _make_product(store, name="Visible Product")
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)

    response = client.get(path)

    assert response.status_code == 200


@pytest.mark.django_db
def test_api_key_can_create_order_valid_payload():
    store = _make_store("Orders")
    product = _make_product(store, price=150, stock=10)
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
        order = Order.objects.get(public_id=response.data["public_id"])
    assert order.store_id == store.id
    assert str(order.subtotal) == "300.00"


@pytest.mark.django_db
def test_api_key_order_with_fake_price_field_fails():
    store = _make_store("Orders")
    product = _make_product(store, price=150, stock=10)
    zone = _make_zone(store)
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)

    payload = {
        "shipping_zone_public_id": zone.public_id,
        "shipping_name": "Alice",
        "phone": "01712345678",
        "email": "alice@example.com",
        "shipping_address": "Dhaka",
        "products": [{"product_public_id": product.public_id, "quantity": 2, "price": "1.00"}],
    }
    response = client.post("/api/v1/orders/", payload, format="json")

    assert response.status_code == 400


@pytest.mark.django_db
def test_api_key_order_with_other_store_product_fails():
    store_a = _make_store("Store A")
    store_b = _make_store("Store B")
    product_b = _make_product(store_b, price=200, stock=5)
    zone_a = _make_zone(store_a)
    _key_row, api_key_a = create_store_api_key(store_a, name="frontend-a")
    client = _api_key_client(api_key_a)

    payload = {
        "shipping_zone_public_id": zone_a.public_id,
        "shipping_name": "Alice",
        "phone": "01712345678",
        "email": "alice@example.com",
        "shipping_address": "Dhaka",
        "products": [{"product_public_id": product_b.public_id, "quantity": 1}],
    }
    response = client.post("/api/v1/orders/", payload, format="json")

    assert response.status_code == 400


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/orders/"),
        ("get", "/api/v1/orders/non-existent/"),
        ("patch", "/api/v1/orders/non-existent/"),
        ("get", "/api/v1/customers/me/"),
        ("get", "/api/v1/admin/analytics/overview/"),
    ],
)
def test_api_key_restricted_access_is_blocked(method, path):
    store = _make_store("Restricted")
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)

    response = getattr(client, method)(path, {}, format="json")

    assert response.status_code in {401, 403, 404, 405}


@pytest.mark.django_db
def test_auth_missing_invalid_and_revoked_api_key():
    store = _make_store("Auth")
    _make_product(store, name="Auth Product")
    key_row, valid_key = create_store_api_key(store, name="frontend")

    client = APIClient()
    missing = client.get("/api/v1/products/")
    assert missing.status_code == 401

    invalid = client.get("/api/v1/products/", HTTP_AUTHORIZATION="Bearer ak_live_invalid")
    assert invalid.status_code == 401

    revoke_store_api_key(key_row)
    revoked = client.get("/api/v1/products/", HTTP_AUTHORIZATION=f"Bearer {valid_key}")
    assert revoked.status_code == 401


@pytest.mark.django_db
def test_cross_tenant_order_access_fails_with_api_key():
    store_a = _make_store("Store A")
    store_b = _make_store("Store B")
    zone_b = _make_zone(store_b)
    order_b = Order.objects.create(
        store=store_b,
        order_number="SECURE0001",
        email="b@example.com",
        shipping_name="Bob",
        shipping_address="Addr",
        phone="01700000000",
        shipping_zone=zone_b,
    )
    _key_row, api_key_a = create_store_api_key(store_a, name="frontend-a")
    client = _api_key_client(api_key_a)

    response = client.get(f"/api/v1/orders/{order_b.public_id}/", {"email": "b@example.com"})

    assert response.status_code in {401, 403}


def test_api_key_view_scan_raises_when_allow_flag_missing():
    class MissingAllowView(APIView):
        permission_classes = [IsStorefrontAPIKey]
        authentication_classes = []

    patterns = [path("broken/", MissingAllowView.as_view())]
    with pytest.raises(RuntimeError):
        validate_storefront_api_key_view_flags(patterns=patterns)


def test_maybe_validate_scan_raises_in_debug(monkeypatch, settings):
    settings.DEBUG = True
    settings.TESTING = False
    store_api_key_auth._API_KEY_VIEW_SCAN_DONE = False

    def _raise():
        raise RuntimeError("missing allow flag")

    monkeypatch.setattr(store_api_key_auth, "validate_storefront_api_key_view_flags", _raise)
    with pytest.raises(RuntimeError):
        maybe_validate_storefront_api_key_view_flags()


def test_maybe_validate_scan_raises_in_test_mode(monkeypatch, settings):
    settings.DEBUG = False
    settings.TESTING = True
    store_api_key_auth._API_KEY_VIEW_SCAN_DONE = False

    def _raise():
        raise RuntimeError("missing allow flag")

    monkeypatch.setattr(store_api_key_auth, "validate_storefront_api_key_view_flags", _raise)
    with pytest.raises(RuntimeError):
        maybe_validate_storefront_api_key_view_flags()


def test_maybe_validate_scan_raises_in_prod(monkeypatch, settings):
    settings.DEBUG = False
    settings.TESTING = False
    store_api_key_auth._API_KEY_VIEW_SCAN_DONE = False
    called = {"value": False}

    def _raise():
        called["value"] = True
        raise RuntimeError("missing allow flag")

    monkeypatch.setattr(store_api_key_auth, "validate_storefront_api_key_view_flags", _raise)
    with pytest.raises(RuntimeError):
        maybe_validate_storefront_api_key_view_flags()
    assert called["value"] is True


@pytest.mark.django_db
def test_admin_can_list_orders_with_jwt():
    store = _make_store("Admin Orders")
    zone = _make_zone(store)
    Order.objects.create(
        store=store,
        order_number="ADM0001",
        email="buyer@example.com",
        shipping_name="Buyer",
        shipping_address="Addr",
        phone="01700000000",
        shipping_zone=zone,
    )
    client = _admin_client_for_store(store)
    response = client.get("/api/v1/orders/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_order_payload_edge_cases_fail_safely():
    store = _make_store("Edge")
    product = _make_product(store, stock=50)
    zone = _make_zone(store)
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)

    base_payload = {
        "shipping_zone_public_id": zone.public_id,
        "shipping_name": "Edge User",
        "phone": "01712345678",
        "email": "edge@example.com",
        "shipping_address": "Address",
    }

    negative_qty = {**base_payload, "products": [{"product_public_id": product.public_id, "quantity": -1}]}
    assert client.post("/api/v1/orders/", negative_qty, format="json").status_code == 400

    huge_qty = {**base_payload, "products": [{"product_public_id": product.public_id, "quantity": 999999}]}
    assert client.post("/api/v1/orders/", huge_qty, format="json").status_code == 400

    sql_like_qty = {**base_payload, "products": [{"product_public_id": product.public_id, "quantity": "1 OR 1=1"}]}
    assert client.post("/api/v1/orders/", sql_like_qty, format="json").status_code == 400

    hidden_fields = {
        **base_payload,
        "products": [{"product_public_id": product.public_id, "quantity": 1}],
        "total": "0.01",
        "discount": "999",
    }
    assert client.post("/api/v1/orders/", hidden_fields, format="json").status_code == 400

    missing_required = {"products": [{"product_public_id": product.public_id, "quantity": 1}]}
    assert client.post("/api/v1/orders/", missing_required, format="json").status_code == 400


def test_production_safety_gate_blocks_override_flags(settings):
    settings.DEBUG = False
    settings.TESTING = False
    settings.SECURITY_INTERNAL_OVERRIDE_ALLOWED = True
    with pytest.raises(RuntimeError):
        enforce_production_override_safety()


@pytest.mark.django_db
def test_auto_route_policy_with_api_key():
    store = _make_store("Route Policy")
    _make_product(store, name="Policy Product")
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)

    expected_allowed = {
        ("GET", "/api/v1/products/"),
        ("GET", "/api/v1/categories/"),
        ("GET", "/api/v1/banners/"),
        ("GET", "/api/v1/shipping/options/"),
    }
    for prefix, methods in STORE_FRONTEND_ROUTE_POLICY:
        for method in methods:
            # Skip state-changing routes that need setup payload to avoid false negatives.
            if method == "GET" and prefix not in {
                "/api/v1/orders/",
            }:
                expected_allowed.add((method, prefix))

    checked_routes = sorted(expected_allowed)
    for method, path in checked_routes:
        response = client.generic(method, path)
        assert response.status_code in {200, 201, 204, 400, 404}

    blocked_routes = [
        ("GET", "/api/v1/orders/"),
        ("GET", "/api/v1/orders/non-existent/"),
        ("GET", "/api/v1/admin/orders/"),
        ("GET", "/api/v1/admin/customers/"),
        ("GET", "/api/v1/admin/search/"),
        ("GET", "/api/v1/settings/network/api-keys/"),
    ]
    for method, path in blocked_routes:
        response = client.generic(method, path)
        assert response.status_code in {401, 403, 404}


@pytest.mark.django_db
def test_api_key_is_explicitly_blocked_on_exempt_dashboard_routes():
    store = _make_store("Exempt Block")
    _key_row, api_key = create_store_api_key(store, name="frontend")
    client = _api_key_client(api_key)

    response = client.get("/api/v1/settings/network/api-keys/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_without_membership_cannot_switch_tenants_via_header():
    store_a = _make_store("Tenant A")
    store_b = _make_store("Tenant B")
    zone_b = _make_zone(store_b)
    Order.objects.create(
        store=store_b,
        order_number="TENANTB0001",
        email="buyer@example.com",
        shipping_name="Buyer",
        shipping_address="Addr",
        phone="01700000000",
        shipping_zone=zone_b,
    )
    user = User.objects.create_user(
        email="staff-no-membership@example.com",
        password="pass1234",
    )
    user.is_verified = True
    user.is_staff = True
    user.save(update_fields=["is_verified", "is_staff"])
    StoreMembership.objects.create(
        user=user,
        store=store_a,
        role=StoreMembership.Role.OWNER,
        is_active=True,
    )
    client = APIClient()
    client.force_authenticate(user=user)
    client.credentials(HTTP_X_STORE_PUBLIC_ID=store_b.public_id)

    response = client.get("/api/v1/orders/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_variant_stock_sync_updates_product_total_consistently():
    store = _make_store("Stock Sync")
    with tenant_scope_from_store(store=store, reason="test fixture"):
        category = Category.objects.create(
            store=store,
            name="Stock Category",
            slug="stock-category",
        )
        product = Product.objects.create(
            store=store,
            category=category,
            name="Variant Product",
            price=120,
            stock=0,
            status=Product.Status.ACTIVE,
            is_active=True,
        )
        v1 = ProductVariant.objects.create(
            product=product,
            sku="sku-v1",
            is_active=True,
        )
        v2 = ProductVariant.objects.create(
            product=product,
            sku="sku-v2",
            is_active=True,
        )
        Inventory.objects.create(product=product, variant=v1, quantity=3)
        Inventory.objects.create(product=product, variant=v2, quantity=7)

        sync_product_stock_from_variants(product.id)
        product.refresh_from_db()
        assert product.stock == 10

        Inventory.objects.filter(product=product, variant=v1).update(quantity=5)
        Inventory.objects.filter(product=product, variant=v2).update(quantity=1)
        sync_product_stock_from_variants(product.id)
        product.refresh_from_db()
        assert product.stock == 6


@pytest.mark.django_db
def test_terminal_status_transition_restores_stock_once_per_item_reason():
    store = _make_store("Restore Once")
    product = _make_product(store, stock=0)
    zone = _make_zone(store)
    inv_row = Inventory.objects.get(product=product, variant__isnull=True)
    inv_row.quantity = 3
    inv_row.save(update_fields=["quantity"])
    order = Order.objects.create(
        store=store,
        order_number="RESTORE0001",
        email="buyer@example.com",
        shipping_name="Buyer",
        shipping_address="Addr",
        phone="01700000000",
        shipping_zone=zone,
        status=Order.Status.PENDING,
    )
    item = OrderItem.objects.create(order=order, product=product, quantity=2, price=product.price)
    with tenant_scope_from_store(store=store, reason="test fixture"):
        transition_order_status(order=order, to_status=Order.Status.FAILED, note="payment-fail", actor_label="test")
        transition_order_status(order=order, to_status=Order.Status.FAILED, note="retry", actor_label="test")

    inv = Inventory.objects.get(product=product, variant__isnull=True)
    assert inv.quantity == 5
    assert (
        StockRestoreLog.objects.filter(
            order=order,
            order_item=item,
            reason=Order.Status.FAILED,
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_admin_product_patch_rejects_direct_stock_mutation():
    store = _make_store("No Direct Stock Patch")
    product = _make_product(store, stock=10)
    client = _admin_client_for_store(store)

    response = client.patch(
        f"/api/v1/admin/products/{product.public_id}/",
        {"stock": 999},
        format="json",
    )
    assert response.status_code == 400
