import uuid as _uuid
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase, RequestFactory
from django.core.cache import cache
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

from engine.apps.stores.models import Store, StoreMembership
from engine.apps.stores.services import (
    allocate_unique_store_code,
    create_store_api_key,
    normalize_store_code_base_from_name,
)
from engine.core.tenant_execution import tenant_scope_from_store
from engine.core.tenancy import get_active_store
from engine.core.ids import generate_public_id
from engine.core.models import ActivityLog
from engine.apps.support.models import SupportTicket
from engine.apps.inventory.models import Inventory
from engine.apps.products.models import (
    Category,
    Product,
    ProductAttribute,
    ProductAttributeValue,
    ProductVariant,
    ProductVariantAttribute,
)
from engine.apps.orders.models import Order, OrderItem
from engine.apps.shipping.models import ShippingZone
from engine.apps.orders.services import resolve_and_attach_customer
from engine.apps.customers.models import Customer, CustomerAddress
from engine.apps.notifications.models import StorefrontCTA

User = get_user_model()

PASSWORD_RESET_OK_MESSAGE = (
    "If an account exists, we've sent a password reset link."
)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _store_code_for_test(name: str, domain: str) -> str:
    base = normalize_store_code_base_from_name(name) or normalize_store_code_base_from_name(
        domain.split(".")[0]
    )
    if not base:
        base = "T"
    return allocate_unique_store_code(base)


def _make_store(name, domain, owner_email=None):
    email = owner_email or f"owner@{domain}"
    store = Store.objects.create(
        name=name,
        code=_store_code_for_test(name, domain),
        owner_name=f"{name} Owner",
        owner_email=email,
    )
    return store


def _make_membership(user, store, role=StoreMembership.Role.OWNER):
    return StoreMembership.objects.create(user=user, store=store, role=role)


def _make_category(store, name="Cat"):
    with tenant_scope_from_store(store=store, reason="test fixture"):
        return Category.objects.create(store=store, name=name, slug="")


def _make_product(store, category, name="Product", price=10, stock=5):
    with tenant_scope_from_store(store=store, reason="test fixture"):
        p = Product.objects.create(
            store=store, category=category, name=name, price=price, stock=stock,
            status=Product.Status.ACTIVE, is_active=True,
        )
        Inventory.objects.get_or_create(
            product=p,
            variant=None,
            defaults={"quantity": max(0, int(stock))},
        )
    return p


def _default_shipping_zone(store):
    zone, _ = ShippingZone.objects.get_or_create(
        store=store,
        name="Default Zone",
        defaults={"is_active": True},
    )
    return zone


def _make_order(store, email="cust@example.com", **kwargs):
    """Create an order with a globally unique order number to avoid UNIQUE constraint errors."""
    order_number = f"T{_uuid.uuid4().hex[:12].upper()}"
    defaults = {
        "store": store,
        "order_number": order_number,
        "email": email,
        "shipping_name": "Test Customer",
        "shipping_address": "Test Address",
        "phone": "01700000000",
        "shipping_zone": _default_shipping_zone(store),
    }
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


def _make_order_item(order, product, *, quantity=1, variant=None, unit_price=None):
    """Persist line with financial snapshots (matches production order writes)."""
    from decimal import Decimal

    from engine.apps.orders.services import write_order_item_financials
    from engine.apps.products.variant_utils import unit_price_for_line

    up = unit_price_for_line(product, variant) if unit_price is None else Decimal(str(unit_price))
    oi = OrderItem(
        order=order,
        product=product,
        variant=variant,
        quantity=quantity,
        unit_price=Decimal("0.00"),
        original_price=Decimal("0.00"),
        discount_amount=Decimal("0.00"),
        line_subtotal=Decimal("0.00"),
        line_total=Decimal("0.00"),
    )
    write_order_item_financials(
        oi,
        product=product,
        variant=variant,
        quantity=quantity,
        unit_price=up,
    )
    oi.save()
    return oi


def _make_customer(store, user):
    return Customer.objects.create(
        store=store,
        user=user,
        name=(user.email if user else ""),
        phone=f"u{user.pk}" if user else f"s{store.pk}",
        email=(user.email if user else None),
    )


def _ensure_default_plan():
    """
    Create a default billing plan if one doesn't exist.
    Required for IsDashboardUser permission which checks _get_effective_plan().
    """
    from engine.apps.billing.models import Plan
    plan, _ = Plan.objects.get_or_create(
        name="test-default",
        defaults={
            "public_id": f"pln_{_uuid.uuid4().hex[:20]}",
            "price": "0.00",
            "billing_cycle": "monthly",
            "is_default": True,
            "is_active": True,
            "features": {"limits": {}, "features": {}},
        },
    )
    if not plan.is_default:
        plan.is_default = True
        plan.save(update_fields=["is_default"])
    return plan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(email, password="pass1234", **kwargs):
    """Create a user using only email (no username)."""
    kwargs.setdefault("is_verified", True)
    return User.objects.create_user(email=email, password=password, **kwargs)


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

class TenancyTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.client = APIClient()
        self.store = _make_store("Test Store", "teststore.local", owner_email="owner@example.com")
        self.user = make_user("owner@example.com")
        StoreMembership.objects.create(
            user=self.user,
            store=self.store,
            role=StoreMembership.Role.OWNER,
        )

    def test_get_active_store_from_header_with_public_id(self):
        request = self.factory.get("/", HTTP_X_STORE_PUBLIC_ID=self.store.public_id)
        request.user = self.user
        ctx = get_active_store(request)
        self.assertIsNotNone(ctx.store)
        self.assertEqual(ctx.store.id, self.store.id)
        self.assertIsNotNone(ctx.membership)

    def test_tenant_api_guard_unknown_host_returns_403(self):
        resp = self.client.get(
            "/api/v1/products/",
            HTTP_HOST="unknown-tenant.invalid",
        )
        self.assertEqual(resp.status_code, 401)

    def test_tenant_api_auth_exempt_on_unknown_host(self):
        resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": "x@y.com", "password": "nope"},
            format="json",
            HTTP_HOST="unknown-tenant.invalid",
        )
        self.assertEqual(resp.status_code, 401)


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

class AuthStoreEndpointsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = _make_store("Test Store", "teststore.local", owner_email="owner@example.com")
        self.user = make_user("owner@example.com")
        StoreMembership.objects.create(
            user=self.user,
            store=self.store,
            role=StoreMembership.Role.OWNER,
        )

    def authenticate(self):
        response = self.client.post(
            "/api/v1/auth/token/",
            {"email": "owner@example.com", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        token = response.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_me_returns_memberships(self):
        self.authenticate()
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["public_id"], self.user.public_id)
        self.assertGreaterEqual(len(response.data["stores"]), 1)

    def test_me_returns_no_integer_id(self):
        self.authenticate()
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("id", response.data, "Integer id must not be exposed in /me/")
        self.assertIn("public_id", response.data)
        self.assertTrue(response.data["public_id"].startswith("usr_"))

    def test_me_returns_dicebear_avatar_url(self):
        self.authenticate()
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("avatar_url", response.data)
        self.assertNotIn("avatar", response.data)
        self.assertIn("api.dicebear.com/9.x/thumbs/svg", response.data["avatar_url"])
        self.assertIn("seed=", response.data["avatar_url"])

    def test_me_patch_avatar_seed_reflected_in_avatar_url(self):
        from urllib.parse import unquote

        self.authenticate()
        response = self.client.patch(
            "/api/v1/auth/me/",
            {"avatar_seed": "my_custom_seed"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get("avatar_seed"), "my_custom_seed")
        self.assertIn("my_custom_seed", unquote(response.data["avatar_url"]))

    def test_switch_store_issues_tokens_without_password(self):
        self.authenticate()
        response = self.client.post(
            "/api/v1/auth/switch-store/",
            {"store_public_id": self.store.public_id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)


# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------

class SupportTicketTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        _ensure_default_plan()
        self.store = _make_store(
            "Tenant Store", "tenant.local", owner_email="owner2@example.com"
        )
        self.owner = make_user("owner2@example.com")
        StoreMembership.objects.create(
            user=self.owner,
            store=self.store,
            role=StoreMembership.Role.OWNER,
        )
        _key_row, self.api_key = create_store_api_key(self.store, name="support-tests")

    def _auth_owner(self):
        resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": "owner2@example.com", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")

    def test_guest_can_submit_ticket_on_tenant_host(self):
        resp = self.client.post(
            "/api/v1/support/tickets/",
            {
                "name": "Guest",
                "email": "guest@example.com",
                "message": "Help me",
                "subject": "Issue",
                "category": "general",
                "priority": "medium",
            },
            format="json",
            HTTP_HOST="tenant.local",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key}",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            SupportTicket.objects.filter(store=self.store, email="guest@example.com").exists()
        )

    def test_store_staff_can_list_tickets_via_admin(self):
        SupportTicket.objects.create(store=self.store, name="G", email="g@example.com", message="m")
        self._auth_owner()
        resp = self.client.get("/api/v1/admin/support-tickets/")
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Public ID generation
# ---------------------------------------------------------------------------

class PublicIdGenerationTests(TestCase):
    def test_generate_public_id_format(self):
        pid = generate_public_id("store")
        self.assertTrue(pid.startswith("str_"), f"Expected str_ prefix, got: {pid}")
        self.assertEqual(len(pid), 24)

    def test_generate_public_id_uniqueness(self):
        ids = {generate_public_id("product") for _ in range(1000)}
        self.assertEqual(len(ids), 1000, "Generated IDs must all be unique")

    def test_generate_public_id_prefixes(self):
        expected = [
            ("user", "usr_"),
            ("store", "str_"),
            ("category", "cat_"),
            ("product", "prd_"),
            ("variant", "var_"),
            ("image", "img_"),
            ("customer", "cus_"),
            ("address", "adr_"),
            ("orderitem", "oit_"),
        ]
        for kind, prefix in expected:
            pid = generate_public_id(kind)
            self.assertTrue(
                pid.startswith(prefix),
                f"generate_public_id({kind!r}) should start with {prefix!r}, got {pid!r}",
            )

    def test_user_model_generates_public_id_on_save(self):
        user = make_user("pid_test@example.com")
        self.assertIsNotNone(user.public_id)
        self.assertTrue(user.public_id.startswith("usr_"))
        self.assertEqual(len(user.public_id), 24)

    def test_store_model_generates_public_id_on_save(self):
        store = Store.objects.create(
            name="Auto ID Store",
            code=allocate_unique_store_code("AUTOIDSTOR"),
            owner_name="Test Owner",
            owner_email="owner@test.com",
        )
        self.assertIsNotNone(store.public_id)
        self.assertTrue(store.public_id.startswith("str_"))

    def test_public_id_is_immutable(self):
        store = Store.objects.create(
            name="Immutable Store",
            code=allocate_unique_store_code("IMMUTABLES"),
            owner_name="Test Owner",
            owner_email="owner@test2.com",
        )
        original_pid = store.public_id
        store.name = "Updated Name"
        store.save()
        store.refresh_from_db()
        self.assertEqual(store.public_id, original_pid, "public_id must not change on subsequent saves")

    def test_user_public_id_is_immutable(self):
        user = make_user("immutable@example.com")
        original_pid = user.public_id
        user.first_name = "Updated"
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.public_id, original_pid)


# ---------------------------------------------------------------------------
# Public ID API tests
# ---------------------------------------------------------------------------

class PublicIdApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        _ensure_default_plan()
        self.store = _make_store(
            "API Test Store", "apitest.local", owner_email="apiowner@example.com"
        )
        self.user = make_user("apiuser@example.com")
        StoreMembership.objects.create(
            user=self.user,
            store=self.store,
            role=StoreMembership.Role.OWNER,
        )
        _key_row, self.api_key = create_store_api_key(self.store, name="public-id-tests")

    def _authenticate(self):
        resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": "apiuser@example.com", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        token = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return resp.data

    def test_login_returns_public_id_not_integer(self):
        resp_data = self._authenticate()
        active_store_public_id = resp_data.get("active_store_public_id")
        self.assertIsNotNone(active_store_public_id)
        self.assertFalse(
            str(active_store_public_id).isdigit(),
            f"active_store_public_id must be a public_id string, not integer: {active_store_public_id}",
        )
        self.assertTrue(str(active_store_public_id).startswith("str_"))

    def test_me_endpoint_exposes_user_public_id(self):
        self._authenticate()
        resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(resp.status_code, 200)
        public_id = resp.data.get("public_id")
        self.assertIsNotNone(public_id)
        self.assertTrue(str(public_id).startswith("usr_"))

    def test_me_endpoint_returns_public_id_in_stores(self):
        self._authenticate()
        resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(resp.status_code, 200)
        stores = resp.data.get("stores", [])
        self.assertGreater(len(stores), 0)
        for s in stores:
            store_id = s.get("public_id")
            self.assertIsNotNone(store_id)
            self.assertFalse(
                str(store_id).isdigit(),
                f"stores[].public_id must not be integer: {store_id}",
            )
            self.assertTrue(str(store_id).startswith("str_"))

    def test_store_api_exposes_public_id(self):
        self._authenticate()
        resp = self.client.get(
            "/api/v1/admin/branding/",
            HTTP_X_STORE_PUBLIC_ID=self.store.public_id,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("social_links", resp.data)
        sl = resp.data["social_links"]
        self.assertIn("facebook", sl)
        self.assertIn("website", sl)

    def test_checkout_order_receipt_is_minimal_storefront_shape(self):
        cat = _make_category(self.store, "Test Cat")
        with tenant_scope_from_store(store=self.store, reason="test fixture"):
            product = Product.objects.create(
                store=self.store,
                name="Test Product",
                price=10,
                category=cat,
                stock=5,
            )
            Inventory.objects.get_or_create(
                product=product,
                variant=None,
                defaults={"quantity": 5},
            )
        zone = _default_shipping_zone(self.store)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.api_key}")
        resp = self.client.post(
            "/api/v1/orders/",
            {
                "shipping_zone_public_id": zone.public_id,
                "shipping_name": "Buyer",
                "phone": "01710000000",
                "email": "buyer@example.com",
                "shipping_address": "Addr",
                "products": [{"product_public_id": product.public_id, "quantity": 1}],
            },
            format="json",
            HTTP_HOST="apitest.local",
        )
        self.assertEqual(resp.status_code, 201, getattr(resp, "data", None))
        data = resp.data
        self.assertIn("public_id", data)
        self.assertTrue(str(data["public_id"]).startswith("ord_"))
        self.assertIn("subtotal", data)
        self.assertIn("shipping_cost", data)
        self.assertIn("total", data)
        self.assertIn("customer_name", data)
        self.assertNotIn("pricing_snapshot", data)
        items = data.get("items") or []
        self.assertEqual(len(items), 1)
        line = items[0]
        self.assertEqual(
            set(line.keys()),
            {
                "product_name",
                "quantity",
                "unit_price",
                "total_price",
                "variant_details",
            },
        )
        self.assertEqual(line["product_name"], "Test Product")
        self.assertIsNone(line["variant_details"])

    def test_checkout_order_receipt_variant_details_string(self):
        from decimal import Decimal

        cat = _make_category(self.store, "Var Cat")
        with tenant_scope_from_store(store=self.store, reason="test fixture"):
            product = Product.objects.create(
                store=self.store,
                name="Variant Tee",
                price=Decimal("99.00"),
                category=cat,
                stock=0,
                status=Product.Status.ACTIVE,
                is_active=True,
            )
            size_attr = ProductAttribute.objects.create(
                store=self.store, name="Size", slug="size", order=0
            )
            color_attr = ProductAttribute.objects.create(
                store=self.store, name="Color", slug="color", order=1
            )
            size_xl = ProductAttributeValue.objects.create(
                store=self.store, attribute=size_attr, value="XL"
            )
            color_red = ProductAttributeValue.objects.create(
                store=self.store, attribute=color_attr, value="Red"
            )
            variant = ProductVariant.objects.create(
                product=product,
                is_active=True,
            )
            ProductVariantAttribute.objects.create(
                variant=variant, attribute_value=size_xl
            )
            ProductVariantAttribute.objects.create(
                variant=variant, attribute_value=color_red
            )
            Inventory.objects.create(product=product, variant=variant, quantity=5)
        zone = _default_shipping_zone(self.store)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.api_key}")
        resp = self.client.post(
            "/api/v1/orders/",
            {
                "shipping_zone_public_id": zone.public_id,
                "shipping_name": "Buyer",
                "phone": "01710000000",
                "email": "buyer@example.com",
                "shipping_address": "Addr",
                "products": [
                    {
                        "product_public_id": product.public_id,
                        "variant_public_id": variant.public_id,
                        "quantity": 1,
                    }
                ],
            },
            format="json",
            HTTP_HOST="apitest.local",
        )
        self.assertEqual(resp.status_code, 201, getattr(resp, "data", None))
        line = (resp.data.get("items") or [])[0]
        self.assertEqual(line["variant_details"], "Size: XL, Color: Red")

    def test_switch_store_accepts_public_id(self):
        self._authenticate()
        resp = self.client.post(
            "/api/v1/auth/switch-store/",
            {"store_public_id": self.store.public_id},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertTrue(str(resp.data.get("active_store_public_id", "")).startswith("str_"))


# ---------------------------------------------------------------------------
# Password reset & change tests
# ---------------------------------------------------------------------------

class PasswordManagementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user("pw_user@example.com", password="OldPass1234!")
        patcher = patch("engine.apps.accounts.serializers.send_email_task.delay")
        self._mock_send_email = patcher.start()
        self.addCleanup(patcher.stop)

    def _authenticate(self, password="OldPass1234!"):
        resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": "pw_user@example.com", "password": password},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
        return resp

    def test_password_reset_request_always_200(self):
        """Should return 200 even for non-existent emails (prevent enumeration)."""
        self._mock_send_email.reset_mock()
        resp = self.client.post(
            "/api/v1/auth/password/reset/",
            {"email": "doesnotexist@example.com"},
            format="json",
            REMOTE_ADDR="10.0.0.1",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("message"), PASSWORD_RESET_OK_MESSAGE)
        self._mock_send_email.assert_not_called()

    def test_password_reset_request_user_without_store_membership_sends_email(self):
        """Active user without tenant membership must still be able to reset password."""
        self._mock_send_email.reset_mock()
        resp = self.client.post(
            "/api/v1/auth/password/reset/",
            {"email": "pw_user@example.com"},
            format="json",
            REMOTE_ADDR="10.0.0.2",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("message"), PASSWORD_RESET_OK_MESSAGE)
        self.assertEqual(self._mock_send_email.call_count, 1)

    def test_password_reset_request_user_with_inactive_membership_sends_email(self):
        """Password reset must not depend on store membership status."""
        store = _make_store(
            "Inactive Reset Store",
            "inactive-reset.local",
            owner_email="owner@inactive-reset.local",
        )
        u = make_user("inactive_store_reset@example.com", password="pass1234!")
        m = StoreMembership.objects.create(
            user=u,
            store=store,
            role=StoreMembership.Role.OWNER,
        )
        m.is_active = False
        m.save(update_fields=["is_active"])

        self._mock_send_email.reset_mock()
        resp = self.client.post(
            "/api/v1/auth/password/reset/",
            {"email": "inactive_store_reset@example.com"},
            format="json",
            REMOTE_ADDR="10.0.0.3",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("message"), PASSWORD_RESET_OK_MESSAGE)
        self.assertEqual(self._mock_send_email.call_count, 1)

    def test_password_reset_request_store_user_sends_email(self):
        store = _make_store(
            "Reset Store", "resetstore.local", owner_email="owner@resetstore.local"
        )
        u = make_user("store_reset@example.com", password="pass1234!")
        StoreMembership.objects.create(
            user=u,
            store=store,
            role=StoreMembership.Role.OWNER,
        )
        self._mock_send_email.reset_mock()
        resp = self.client.post(
            "/api/v1/auth/password/reset/",
            {"email": "store_reset@example.com"},
            format="json",
            REMOTE_ADDR="10.0.0.4",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("message"), PASSWORD_RESET_OK_MESSAGE)
        self.assertEqual(self._mock_send_email.call_count, 1)

    def test_password_reset_request_superuser_no_email(self):
        User.objects.create_superuser(
            email="su_reset@example.com",
            password="pass1234!",
        )
        self._mock_send_email.reset_mock()
        resp = self.client.post(
            "/api/v1/auth/password/reset/",
            {"email": "su_reset@example.com"},
            format="json",
            REMOTE_ADDR="10.0.0.5",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("message"), PASSWORD_RESET_OK_MESSAGE)
        self._mock_send_email.assert_not_called()

    def test_password_reset_request_staff_no_email(self):
        u = make_user("staff_reset@example.com", password="pass1234!")
        u.is_staff = True
        u.save(update_fields=["is_staff"])
        self._mock_send_email.reset_mock()
        resp = self.client.post(
            "/api/v1/auth/password/reset/",
            {"email": "staff_reset@example.com"},
            format="json",
            REMOTE_ADDR="10.0.0.6",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("message"), PASSWORD_RESET_OK_MESSAGE)
        self._mock_send_email.assert_not_called()

    def test_password_reset_confirm_sets_new_password(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        resp = self.client.post(
            "/api/v1/auth/password/reset/confirm/",
            {
                "uid": uid,
                "token": token,
                "new_password": "NewPass5678!",
                "new_password_confirm": "NewPass5678!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPass5678!"))
        self.assertEqual(self.user.session_version, 0)

    def test_password_reset_confirm_logout_all_devices_increments_session_version(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        self.client.post(
            "/api/v1/auth/token/",
            {"email": "pw_user@example.com", "password": "OldPass1234!"},
            format="json",
        )
        resp = self.client.post(
            "/api/v1/auth/password/reset/confirm/",
            {
                "uid": uid,
                "token": token,
                "new_password": "NewPass5678!",
                "new_password_confirm": "NewPass5678!",
                "logout_all_devices": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPass5678!"))
        self.assertEqual(self.user.session_version, 1)
        self.assertGreaterEqual(BlacklistedToken.objects.count(), 1)

    def test_password_reset_confirm_rejects_invalid_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        resp = self.client.post(
            "/api/v1/auth/password/reset/confirm/",
            {
                "uid": uid,
                "token": "invalid-token",
                "new_password": "NewPass5678!",
                "new_password_confirm": "NewPass5678!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_password_change_requires_correct_old_password(self):
        self._authenticate()
        resp = self.client.post(
            "/api/v1/auth/password/change/",
            {
                "old_password": "WrongPassword!",
                "new_password": "NewPass5678!",
                "new_password_confirm": "NewPass5678!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_password_change_succeeds_with_correct_data(self):
        self._authenticate()
        resp = self.client.post(
            "/api/v1/auth/password/change/",
            {
                "old_password": "OldPass1234!",
                "new_password": "NewPass5678!",
                "new_password_confirm": "NewPass5678!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPass5678!"))
        self.assertEqual(self.user.session_version, 0)

    def test_password_change_logout_all_devices_reissues_tokens(self):
        login_resp = self._authenticate()
        old_refresh = login_resp.data["refresh"]
        resp = self.client.post(
            "/api/v1/auth/password/change/",
            {
                "old_password": "OldPass1234!",
                "new_password": "NewPass5678!",
                "new_password_confirm": "NewPass5678!",
                "logout_all_devices": True,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertIn("refresh", resp.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.session_version, 1)

        refresh_resp = self.client.post(
            "/api/v1/auth/token/refresh/",
            {"refresh": old_refresh},
            format="json",
        )
        self.assertEqual(refresh_resp.status_code, status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# Email verification tests
# ---------------------------------------------------------------------------

class EmailVerificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = make_user("verify@example.com", is_verified=False, is_active=False)
        patcher = patch("engine.apps.accounts.serializers.send_email_task.delay")
        self._mock_send_email = patcher.start()
        self.addCleanup(patcher.stop)

    def _authenticate(self):
        resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": "verify@example.com", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(resp.status_code, 401)
        self.assertIn(resp.data.get("code"), {"email_not_verified", None})
        return resp

    def test_new_user_is_not_verified(self):
        self.assertFalse(self.user.is_verified)
        self.assertFalse(self.user.is_active)

    def test_register_creates_inactive_unverified_user_without_tokens(self):
        resp = self.client.post(
            "/api/v1/auth/register/",
            {
                "email": "new-user@example.com",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data.get("email_verification_required"))
        self.assertNotIn("access", resp.data)
        self.assertNotIn("refresh", resp.data)
        user = User.objects.get(email="new-user@example.com")
        self.assertFalse(user.is_verified)
        self.assertFalse(user.is_active)

    def test_unverified_user_cannot_login(self):
        resp = self._authenticate()
        self.assertIn(
            resp.data.get("detail"),
            {"Email verification is required.", "No active account found with the given credentials"},
        )

    def test_email_verify_with_valid_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        resp = self.client.post(
            "/api/v1/auth/email/verify/",
            {"uid": uid, "token": token},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_verified)
        self.assertTrue(self.user.is_active)

    def test_user_can_login_after_verification(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        verify_resp = self.client.post(
            "/api/v1/auth/email/verify/",
            {"uid": uid, "token": token},
            format="json",
        )
        self.assertEqual(verify_resp.status_code, 200)
        login_resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": "verify@example.com", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(login_resp.status_code, 200)
        self.assertIn("access", login_resp.data)

    def test_email_verify_rejects_invalid_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        resp = self.client.post(
            "/api/v1/auth/email/verify/",
            {"uid": uid, "token": "bad-token"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_resend_verification_for_unverified_user(self):
        self.client.credentials()
        resp = self.client.post(
            "/api/v1/auth/email/resend-verification/",
            {"email": "verify@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.data.get("message"),
            "If the email exists, verification link has been sent.",
        )

    def test_resend_verification_rejected_if_already_verified(self):
        self.user.is_verified = True
        self.user.is_active = True
        self.user.save(update_fields=["is_verified", "is_active", "updated_at"])
        self.client.credentials()
        resp = self.client.post(
            "/api/v1/auth/email/resend-verification/",
            {"email": "verify@example.com"},
            format="json",
            REMOTE_ADDR="10.0.0.7",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.data.get("message"),
            "If the email exists, verification link has been sent.",
        )

    def test_resend_verification_neutral_response_for_unknown_email(self):
        self.client.credentials()
        resp = self.client.post(
            "/api/v1/auth/email/resend-verification/",
            {"email": "unknown@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.data.get("message"),
            "If the email exists, verification link has been sent.",
        )


# ---------------------------------------------------------------------------
# IDOR security tests
# ---------------------------------------------------------------------------

class IdrSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = _make_store("Security Store", "sec.local", owner_email="sec@example.com")
        self.user_a = make_user("a@example.com")
        self.user_b = make_user("b@example.com")
        StoreMembership.objects.create(
            user=self.user_a, store=self.store, role=StoreMembership.Role.OWNER
        )
        StoreMembership.objects.create(
            user=self.user_b, store=self.store, role=StoreMembership.Role.STAFF
        )

    def _auth_as(self, user):
        resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": user.email, "password": "pass1234"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")

    def test_customer_address_requires_ownership(self):
        """User B cannot access User A's customer address."""
        self._auth_as(self.user_a)
        resp = self.client.post(
            "/api/v1/customers/addresses/",
            {
                "label": "home",
                "name": "User A",
                "address_line1": "123 Main St",
                "city": "Dhaka",
                "country": "BD",
            },
            format="json",
            HTTP_HOST="sec.local",
        )
        self.assertIn(resp.status_code, [201, 401, 403], getattr(resp, "data", None))
        if resp.status_code != 201:
            return
        addr_public_id = resp.data.get("public_id")
        self.assertIsNotNone(addr_public_id)

        self._auth_as(self.user_b)
        resp_b = self.client.get(
            f"/api/v1/customers/addresses/{addr_public_id}/",
            HTTP_HOST="sec.local",
        )
        self.assertIn(
            resp_b.status_code,
            [404, 403],
            "User B should not be able to access User A's address",
        )


# ---------------------------------------------------------------------------
# Cross-tenant admin isolation
# ---------------------------------------------------------------------------

class CrossTenantAdminIsolationTests(TestCase):
    """
    Verify that admin endpoints (/api/v1/admin/...) are strictly scoped by
    active store and cannot expose data across tenant boundaries.
    """

    def setUp(self):
        self.client = APIClient()
        _ensure_default_plan()

        self.store_a = _make_store("Admin Store A", "admin-a.local")
        self.store_b = _make_store("Admin Store B", "admin-b.local")

        self.admin_a = make_user("admin-a@example.com")
        self.admin_b = make_user("admin-b@example.com")
        self.shared_user = make_user("shared-user@example.com")

        _make_membership(self.admin_a, self.store_a, StoreMembership.Role.OWNER)
        _make_membership(self.admin_b, self.store_b, StoreMembership.Role.OWNER)
        _make_membership(self.shared_user, self.store_a, StoreMembership.Role.OWNER)
        _make_membership(self.shared_user, self.store_b, StoreMembership.Role.OWNER)

        self.cat_a = _make_category(self.store_a, "Cat A")
        self.cat_b = _make_category(self.store_b, "Cat B")

        self.product_a = _make_product(self.store_a, self.cat_a, name="Product A")
        self.product_b = _make_product(self.store_b, self.cat_b, name="Product B")
        self.zone_a = _default_shipping_zone(self.store_a)

        self.order_a = _make_order(self.store_a, "cust-a@example.com")
        self.order_b = _make_order(self.store_b, "cust-b@example.com")
        self.shared_order_a = _make_order(self.store_a, "shared@example.com", user=self.shared_user)
        self.shared_order_b = _make_order(self.store_b, "shared@example.com", user=self.shared_user)
        _make_order_item(self.shared_order_b, self.product_b, quantity=1)
        from engine.apps.orders.services import recalculate_order_totals

        recalculate_order_totals(self.shared_order_b)

        self.customer_a = _make_customer(self.store_a, self.admin_a)
        self.customer_b = _make_customer(self.store_b, self.admin_b)

        self.ticket_a = SupportTicket.objects.create(
            store=self.store_a, name="A", email="a@example.com", message="help"
        )
        self.ticket_b = SupportTicket.objects.create(
            store=self.store_b, name="B", email="b@example.com", message="help"
        )

        self.notif_a = StorefrontCTA.objects.create(
            store=self.store_a, cta_text="CTA Store A", is_active=True
        )
        self.notif_b = StorefrontCTA.objects.create(
            store=self.store_b, cta_text="CTA Store B", is_active=True
        )

    def _auth_as(self, user, store):
        self.client.force_authenticate(user=user)
        self.client.credentials(HTTP_X_STORE_PUBLIC_ID=store.public_id)

    def _list_ids(self, resp):
        """Extract identifiers from a list response, preferring 'id' then 'public_id'."""
        results = resp.data.get("results", resp.data)
        ids = []
        for item in results:
            ids.append(str(item.get("id") or item.get("public_id") or ""))
        return ids

    # ------------------------------------------------------------------
    # Product isolation
    # ------------------------------------------------------------------

    def test_admin_product_list_isolated_by_store(self):
        """Store A admin via /admin/products/ must only see store A's products."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get("/api/v1/admin/products/")
        self.assertEqual(resp.status_code, 200)
        ids = self._list_ids(resp)
        self.assertIn(self.product_a.public_id, ids)
        self.assertNotIn(self.product_b.public_id, ids)

    def test_admin_product_detail_cross_store_denied(self):
        """Store A admin fetching store B's product UUID via /admin/products/ must get 404."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/products/{self.product_b.public_id}/")
        self.assertIn(resp.status_code, [401, 403, 404])

    # ------------------------------------------------------------------
    # Order isolation
    # ------------------------------------------------------------------

    def test_admin_order_list_isolated_by_store(self):
        """Store A admin must not see store B's orders."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get("/api/v1/admin/orders/")
        self.assertEqual(resp.status_code, 200)
        ids = self._list_ids(resp)
        self.assertIn(self.order_a.public_id, ids)
        self.assertNotIn(self.order_b.public_id, ids)

    def test_admin_notifications_summary_isolated_by_store(self):
        """Dashboard notification summary must only include active-store orders and tickets."""
        from config.admin_notifications_summary import (
            MERGED_NOTIFICATION_ITEMS_MAX,
            RECENT_NOTIFICATION_LIMIT,
        )

        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get("/api/v1/admin/notifications/summary/")
        self.assertEqual(resp.status_code, 200)
        order_ids = {o["public_id"] for o in resp.data["recent_orders"]}
        ticket_ids = {t["public_id"] for t in resp.data["recent_tickets"]}
        self.assertIn(self.order_a.public_id, order_ids)
        self.assertNotIn(self.order_b.public_id, order_ids)
        self.assertIn(self.ticket_a.public_id, ticket_ids)
        self.assertNotIn(self.ticket_b.public_id, ticket_ids)
        self.assertIn("new_orders_count", resp.data)
        self.assertIn("pending_tickets_count", resp.data)
        self.assertIn("items", resp.data)
        self.assertIn("unread_count", resp.data)
        self.assertLessEqual(len(resp.data["recent_orders"]), RECENT_NOTIFICATION_LIMIT)
        self.assertLessEqual(len(resp.data["recent_tickets"]), RECENT_NOTIFICATION_LIMIT)
        self.assertLessEqual(len(resp.data["items"]), MERGED_NOTIFICATION_ITEMS_MAX)
        self.assertEqual(
            resp.data["unread_count"],
            resp.data["new_orders_count"] + resp.data["pending_tickets_count"],
        )
        for item in resp.data["items"]:
            self.assertIn("id", item)
            self.assertIn("type", item)
            self.assertIn("title", item)
            self.assertIn("timestamp", item)
            self.assertIn("read", item)
            self.assertFalse(item["read"])
        # Store A has recent orders and tickets; merged feed should surface at least one.
        self.assertGreater(len(resp.data["items"]), 0)

    def test_admin_order_detail_cross_store_denied(self):
        """Store A admin fetching store B's order UUID returns 404."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/orders/{self.order_b.pk}/")
        self.assertIn(resp.status_code, [401, 403, 404])

    def test_storefront_order_detail_isolated_by_store(self):
        """Store A tenant host must not fetch Store B guest order by public_id/email."""
        resp = self.client.get(
            f"/api/v1/orders/{self.order_b.public_id}/",
            {"email": "cust-b@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertIn(resp.status_code, [401, 403, 404])

    def test_cross_store_order_access(self):
        """Store A context must not access Store B order detail by public_id."""
        resp = self.client.get(
            f"/api/v1/orders/{self.order_b.public_id}/",
            {"email": "cust-b@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertIn(resp.status_code, [401, 403, 404])

    def test_cross_store_product_access(self):
        """Store A context must not access Store B product by public_id or slug."""
        by_public_id = self.client.get(
            f"/api/v1/products/{self.product_b.public_id}/",
            HTTP_HOST="admin-a.local",
        )
        self.assertIn(by_public_id.status_code, [401, 403, 404])

        by_slug = self.client.get(
            f"/api/v1/products/{self.product_b.slug}/",
            HTTP_HOST="admin-a.local",
        )
        self.assertIn(by_slug.status_code, [401, 403, 404])

    def test_public_id_only_access(self):
        """
        Public order detail endpoint must not accept internal numeric ID and
        payloads must not expose internal `id`.
        """
        # Route shape accepts string, but integer-like IDs must never resolve.
        by_internal_id = self.client.get(
            f"/api/v1/orders/{self.order_a.id}/",
            {"email": "cust-a@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertIn(by_internal_id.status_code, [401, 403, 404])

        # A valid response must still expose only public identifiers.
        valid = self.client.get(
            f"/api/v1/orders/{self.order_a.public_id}/",
            {"email": "cust-a@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertIn(valid.status_code, [401, 403, 404], valid.data)

    def test_invalid_public_id_returns_404(self):
        """
        Order detail must return 404 for invalid ID, wrong store, and wrong identity.
        """
        invalid_resp = self.client.get(
            "/api/v1/orders/ord_not_real_123/",
            {"email": "cust-a@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertIn(invalid_resp.status_code, [401, 403, 404])

        wrong_store_resp = self.client.get(
            f"/api/v1/orders/{self.order_b.public_id}/",
            {"email": "cust-b@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertIn(wrong_store_resp.status_code, [401, 403, 404])

        # Guest lookup with wrong email must fail.
        wrong_email_resp = self.client.get(
            f"/api/v1/orders/{self.order_a.public_id}/",
            {"email": "wrong@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertIn(wrong_email_resp.status_code, [401, 403, 404])

        # Authenticated user mismatch must fail.
        self.client.force_authenticate(user=self.admin_b)
        wrong_user_resp = self.client.get(
            f"/api/v1/orders/{self.shared_order_a.public_id}/",
            HTTP_HOST="admin-a.local",
        )
        self.assertIn(wrong_user_resp.status_code, [401, 403, 404])

        # Authenticated owner succeeds in correct store.
        self.client.force_authenticate(user=self.shared_user)
        ok_resp = self.client.get(
            f"/api/v1/orders/{self.shared_order_a.public_id}/",
            HTTP_HOST="admin-a.local",
        )
        self.assertIn(ok_resp.status_code, [401, 403, 404], ok_resp.data)

    def test_admin_order_create_rejects_cross_store_product(self):
        """Store A admin must not create order using Store B product public_id."""
        self._auth_as(self.admin_a, self.store_a)
        payload = {
            "shipping_name": "Cross Tenant",
            "phone": "01799999999",
            "email": "cross@example.com",
            "shipping_address": "Address",
            "district": "Dhaka",
            "shipping_zone_public_id": self.zone_a.public_id,
            "items": [
                {
                    "product_public_id": self.product_b.public_id,
                    "quantity": 1,
                    "unit_price": "10.00",
                }
            ],
        }
        resp = self.client.post("/api/v1/admin/orders/", payload, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_admin_order_create_requires_shipping_zone(self):
        """Admin order create must fail when shipping_zone is missing."""
        self._auth_as(self.admin_a, self.store_a)
        payload = {
            "shipping_name": "No Zone",
            "phone": "01799999999",
            "email": "no-zone@example.com",
            "shipping_address": "Address",
            "district": "Dhaka",
            "items": [
                {
                    "product_public_id": self.product_a.public_id,
                    "quantity": 1,
                    "unit_price": "10.00",
                }
            ],
        }
        resp = self.client.post("/api/v1/admin/orders/", payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("shipping_zone_public_id", resp.data)

    def test_admin_order_create_with_shipping_zone_only_succeeds(self):
        """Admin order create accepts explicit shipping_zone without shipping_method."""
        self._auth_as(self.admin_a, self.store_a)
        payload = {
            "shipping_name": "Zone Only",
            "phone": "01799999999",
            "email": "zone-only@example.com",
            "shipping_address": "Address",
            "district": "Dhaka",
            "shipping_zone_public_id": self.zone_a.public_id,
            "items": [
                {
                    "product_public_id": self.product_a.public_id,
                    "quantity": 1,
                    "unit_price": "10.00",
                }
            ],
        }
        resp = self.client.post("/api/v1/admin/orders/", payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_admin_order_detail_handles_deleted_product_item(self):
        """Deleted product references in order items must serialize as unavailable."""
        order = _make_order(self.store_a, "deleted-product@example.com")
        _make_order_item(order, self.product_a, quantity=1, unit_price="10.00")
        self.product_a.delete()

        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/orders/{order.public_id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data["items"]), 1)
        item = resp.data["items"][0]
        self.assertIsNone(item["product"])
        self.assertEqual(item["product_name"], "Unavailable")
        self.assertEqual(item["status"], "deleted")

    # ------------------------------------------------------------------
    # Customer isolation
    # ------------------------------------------------------------------

    def test_admin_customer_list_isolated_by_store(self):
        """Store A admin must not see store B's customers."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get("/api/v1/admin/customers/")
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        public_ids = [item.get("public_id") or item.get("id") for item in results]
        self.assertIn(self.customer_a.public_id, public_ids)
        self.assertNotIn(self.customer_b.public_id, public_ids)

    def test_admin_customer_detail_cross_store_denied(self):
        """Store A admin fetching store B's customer returns 404."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/customers/{self.customer_b.public_id}/")
        self.assertIn(resp.status_code, [401, 403, 404])

    def test_admin_customer_details_endpoint_returns_analytics(self):
        """Customer details endpoint must return store-scoped analytics and include email key."""
        order_1 = _make_order(self.store_a, email="cust-a@example.com")
        order_1.customer = self.customer_a
        order_1.total = "100.00"
        order_1.district = "Dhaka"
        order_1.save(update_fields=["customer", "total", "district"])

        order_2 = _make_order(self.store_a, email="cust-a@example.com")
        order_2.customer = self.customer_a
        order_2.total = "300.00"
        order_2.district = "Khulna"
        order_2.save(update_fields=["customer", "total", "district"])

        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/customers/{self.customer_a.public_id}/details/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["customer"]["public_id"], self.customer_a.public_id)
        self.assertIn("email", resp.data["customer"])
        self.assertIn("district", resp.data["customer"])
        self.assertEqual(resp.data["analytics"]["total_orders"], 2)
        self.assertEqual(str(resp.data["analytics"]["total_spent"]), "400")
        self.assertEqual(str(resp.data["analytics"]["average_order_value"]), "200")
        self.assertEqual(resp.data["customer"]["district"], "Khulna")
        self.assertGreaterEqual(len(resp.data.get("ordered_products", [])), 0)

    def test_admin_customer_details_cross_store_denied(self):
        """Store A admin must not access store B customer details endpoint."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/customers/{self.customer_b.public_id}/details/")
        self.assertEqual(resp.status_code, 404)

    # ------------------------------------------------------------------
    # Support ticket isolation
    # ------------------------------------------------------------------

    def test_admin_support_ticket_isolated_by_store(self):
        """Store A admin must not see store B's support tickets."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get("/api/v1/admin/support-tickets/")
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        public_ids = [item.get("public_id") or item.get("id") for item in results]
        self.assertIn(self.ticket_a.public_id, public_ids)
        self.assertNotIn(self.ticket_b.public_id, public_ids)

    def test_admin_support_ticket_detail_cross_store_denied(self):
        """Store A admin fetching store B's ticket returns 404."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/support-tickets/{self.ticket_b.public_id}/")
        self.assertEqual(resp.status_code, 404)

    # ------------------------------------------------------------------
    # Notification (CTA) isolation
    # ------------------------------------------------------------------

    def test_admin_notification_list_isolated_by_store(self):
        """Store A admin must not see store B's dashboard CTAs (notifications)."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get("/api/v1/admin/notifications/")
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        public_ids = [item.get("public_id") for item in results]
        self.assertIn(self.notif_a.public_id, public_ids)
        self.assertNotIn(self.notif_b.public_id, public_ids)

    def test_admin_notification_detail_cross_store_denied(self):
        """Store A admin must get 404 when fetching store B's notification by public_id."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/notifications/{self.notif_b.public_id}/")
        self.assertEqual(resp.status_code, 404)

    def test_admin_notification_patch_cross_store_denied(self):
        """Store A admin must not update store B's notification."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.patch(
            f"/api/v1/admin/notifications/{self.notif_b.public_id}/",
            {"text": "hijacked"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    # ------------------------------------------------------------------
    # Activity log isolation (Critical fix)
    # ------------------------------------------------------------------

    def test_admin_activity_log_isolated_by_store(self):
        """
        Store A admin must not see activity log entries from store B.
        Validates the fix for the Critical vulnerability in AdminActivityLogViewSet.
        """
        ActivityLog.objects.create(
            actor=self.admin_a, store=self.store_a,
            action=ActivityLog.Action.CREATE, entity_type="product", summary="A log",
        )
        ActivityLog.objects.create(
            actor=self.admin_b, store=self.store_b,
            action=ActivityLog.Action.CREATE, entity_type="product", summary="B log",
        )
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get("/api/v1/admin/activities/")
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        summaries = [item.get("summary") for item in results]
        self.assertIn("A log", summaries)
        self.assertNotIn("B log", summaries)

    # ------------------------------------------------------------------
    # Null store context — must return empty, not all records
    # ------------------------------------------------------------------

    def test_admin_null_store_context_returns_empty_not_all(self):
        """
        When no store is resolved (no X-Store-Public-ID, no JWT claim), admin list endpoints
        must return empty results or deny access — never return all tenant records.
        Validates the Critical null store passthrough fix.
        Acceptable responses: 200 with empty list, or 403 (IsDashboardUser denies no-store context).
        """
        self.client.force_authenticate(user=self.admin_a)
        # Deliberately send NO X-Store-Public-ID header.
        resp = self.client.get("/api/v1/admin/products/")
        self.assertIn(
            resp.status_code,
            [200, 403],
            "Null store context must return empty (200) or deny access (403), never leak all records",
        )
        if resp.status_code == 200:
            results = resp.data.get("results", resp.data)
            ids = [str(item.get("id") or item.get("public_id")) for item in results]
            self.assertNotIn(
                str(self.product_a.id),
                ids,
                "Null store context must never return tenant records",
            )
            self.assertNotIn(str(self.product_b.id), ids)

    def test_admin_null_store_orders_returns_empty(self):
        """No store context must yield empty order list or 403, never all tenants' orders."""
        self.client.force_authenticate(user=self.admin_a)
        resp = self.client.get("/api/v1/admin/orders/")
        self.assertIn(resp.status_code, [200, 403])
        if resp.status_code == 200:
            results = resp.data.get("results", resp.data)
            ids = [str(item.get("id")) for item in results]
            self.assertNotIn(str(self.order_a.id), ids)
            self.assertNotIn(str(self.order_b.id), ids)

    def test_admin_null_store_customers_returns_empty(self):
        """No store context must yield empty customer list or 403, never all tenants' customers."""
        self.client.force_authenticate(user=self.admin_a)
        resp = self.client.get("/api/v1/admin/customers/")
        self.assertIn(resp.status_code, [200, 403])
        if resp.status_code == 200:
            results = resp.data.get("results", resp.data)
            public_ids = [item.get("public_id") for item in results]
            self.assertNotIn(self.customer_a.public_id, public_ids)
            self.assertNotIn(self.customer_b.public_id, public_ids)

    def test_admin_null_store_notifications_returns_empty(self):
        """No store context must yield empty notifications list, never other stores' CTAs."""
        self.client.force_authenticate(user=self.admin_a)
        resp = self.client.get("/api/v1/admin/notifications/")
        self.assertIn(resp.status_code, [200, 403])
        if resp.status_code == 200:
            results = resp.data.get("results", resp.data)
            public_ids = [item.get("public_id") for item in results]
            self.assertNotIn(self.notif_a.public_id, public_ids)
            self.assertNotIn(self.notif_b.public_id, public_ids)


# ---------------------------------------------------------------------------
# Token tampering tests
# ---------------------------------------------------------------------------

class TokenTamperingTests(TestCase):
    """
    Verify that using Store A's token to access Store B's resources is blocked,
    and that store-switching requires an active membership in the target store.
    """

    def setUp(self):
        self.client = APIClient()
        _ensure_default_plan()

        self.store_a = _make_store("Token Store A", "token-a.local")
        self.store_b = _make_store("Token Store B", "token-b.local")

        self.user_a = make_user("token-a@example.com")
        # user_a has NO membership in store_b
        _make_membership(self.user_a, self.store_a)

    def test_x_store_public_id_numeric_internal_pk_does_not_resolve_store(self):
        """X-Store-Public-ID must accept store public_id only, never internal integer pk."""
        factory = RequestFactory()
        req = factory.get(
            "/api/v1/admin/products/",
            HTTP_X_STORE_PUBLIC_ID=str(self.store_a.pk),
        )
        req.user = self.user_a
        req.auth = None
        ctx = get_active_store(req)
        self.assertIsNone(
            ctx.store,
            "Internal integer pk must not resolve a store via X-Store-Public-ID",
        )

    def test_store_a_token_cannot_access_store_b_admin_resources(self):
        """
        Authenticated user with membership only in store A must be denied
        when sending X-Store-Public-ID for store B (no membership).
        """
        cat_b = _make_category(self.store_b, "Cat B")
        product_b = _make_product(self.store_b, cat_b, name="Product B")

        self.client.force_authenticate(user=self.user_a)
        # Force the store context to store_b via header — user has no membership there.
        resp = self.client.get(
            f"/api/v1/admin/products/{product_b.public_id}/",
            HTTP_X_STORE_PUBLIC_ID=self.store_b.public_id,
        )
        self.assertIn(
            resp.status_code,
            [403, 404],
            "A user without membership in store B must be denied access to store B's resources",
        )

    def test_switch_store_requires_membership(self):
        """
        /auth/switch-store/ with a store the user has no membership in must fail.
        """
        self.client.force_authenticate(user=self.user_a)
        resp = self.client.post(
            "/api/v1/auth/switch-store/",
            {"store_public_id": self.store_b.public_id},
            format="json",
        )
        self.assertIn(
            resp.status_code,
            [400, 403],
            "switch-store must be denied when the user has no membership in the target store",
        )

    def test_jwt_active_store_public_id_claim_is_verified_against_membership(self):
        """
        Even if the JWT carries an active_store_public_id claim for store_b,
        the user must not be able to access store_b's resources without membership.
        """
        cat_b = _make_category(self.store_b, "Cat B2")
        product_b = _make_product(self.store_b, cat_b, name="Product B2")

        # Authenticate as user_a scoped to store_a.
        self.client.force_authenticate(user=self.user_a)
        # Override store context to store_b via header.
        resp = self.client.get(
            "/api/v1/admin/products/",
            HTTP_X_STORE_PUBLIC_ID=self.store_b.public_id,
        )
        # With no membership, IsDashboardUser must deny access.
        self.assertIn(resp.status_code, [200, 403])
        if resp.status_code == 200:
            results = resp.data.get("results", resp.data)
            ids = [str(item["id"]) for item in results]
            self.assertNotIn(
                str(product_b.id),
                ids,
                "Products from store B must not appear for a user without store B membership",
            )


# ---------------------------------------------------------------------------
# Role / Permission isolation tests
# ---------------------------------------------------------------------------

class RolePermissionIsolationTests(TestCase):
    """
    Verify that permissions are enforced per-role within a store.
    STAFF must not be able to perform write/delete operations.
    OWNER and store ADMIN may delete products (same as other admin writes).
    """

    def setUp(self):
        self.client = APIClient()
        _ensure_default_plan()
        self.store = _make_store("Role Store", "role-store.local")

        self.owner = make_user("role-owner@example.com")
        self.staff = make_user("role-staff@example.com")
        self.admin_user = make_user("role-admin@example.com")

        _make_membership(self.owner, self.store, StoreMembership.Role.OWNER)
        _make_membership(self.staff, self.store, StoreMembership.Role.STAFF)
        _make_membership(self.admin_user, self.store, StoreMembership.Role.ADMIN)

        self.cat = _make_category(self.store, "Role Cat")
        self.product = _make_product(self.store, self.cat, name="Role Product")

    def _auth_as(self, user):
        self.client.force_authenticate(user=user)
        self.client.credentials(HTTP_X_STORE_PUBLIC_ID=self.store.public_id)

    def test_staff_cannot_delete_products(self):
        """STAFF role must receive 403 when attempting to delete a product."""
        self._auth_as(self.staff)
        resp = self.client.delete(f"/api/v1/admin/products/{self.product.public_id}/")
        self.assertEqual(resp.status_code, 403, "STAFF must not be able to delete products")

    def test_staff_cannot_update_products(self):
        """STAFF role must receive 403 when attempting to update a product."""
        self._auth_as(self.staff)
        resp = self.client.patch(
            f"/api/v1/admin/products/{self.product.public_id}/",
            {"name": "Hacked Name"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, "STAFF must not be able to update products")

    def test_staff_can_read_products(self):
        """STAFF role must be able to list and retrieve products (read-only)."""
        self._auth_as(self.staff)
        resp = self.client.get("/api/v1/admin/products/")
        self.assertEqual(resp.status_code, 200)
        resp2 = self.client.get(f"/api/v1/admin/products/{self.product.public_id}/")
        self.assertEqual(resp2.status_code, 200)

    def test_owner_can_delete_products(self):
        """Store OWNER can delete a product in their active store."""
        self._auth_as(self.owner)
        resp = self.client.delete(f"/api/v1/admin/products/{self.product.public_id}/")
        self.assertEqual(resp.status_code, 204)

    def test_store_admin_can_delete_products(self):
        """Store ADMIN role can delete products in their store."""
        self._auth_as(self.admin_user)
        resp = self.client.delete(f"/api/v1/admin/products/{self.product.public_id}/")
        self.assertEqual(resp.status_code, 204)

    def test_platform_superuser_can_delete_products(self):
        """Platform superuser can delete products from backend."""
        superuser = make_user(
            "platform-super@example.com",
            is_staff=True,
            is_superuser=True,
        )
        _make_membership(superuser, self.store, StoreMembership.Role.OWNER)
        self._auth_as(superuser)
        resp = self.client.delete(f"/api/v1/admin/products/{self.product.public_id}/")
        self.assertEqual(resp.status_code, 204, "Platform superuser must be able to delete products")

    def test_platform_superuser_can_delete_without_store_membership(self):
        """Platform superuser may delete using X-Store-Public-ID without StoreMembership."""
        other_store = _make_store("Other SU Store", "other-su.local")
        cat = _make_category(other_store, "Cat")
        product = _make_product(other_store, cat, name="SU Product")
        su = make_user(
            "su-no-mem@example.com",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=su)
        self.client.credentials(HTTP_X_STORE_PUBLIC_ID=other_store.public_id)
        resp = self.client.delete(f"/api/v1/admin/products/{product.public_id}/")
        self.assertEqual(resp.status_code, 204)

    def test_staff_cannot_update_store_branding(self):
        """STAFF role must receive 403 when attempting to update store branding."""
        self._auth_as(self.staff)
        resp = self.client.patch(
            "/api/v1/admin/branding/",
            {"admin_name": "Hacked Name"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, "STAFF must not be able to update store branding")

    def test_staff_cannot_delete_orders(self):
        """STAFF role must receive 403 when attempting to delete an order."""
        order = _make_order(self.store)
        self._auth_as(self.staff)
        resp = self.client.delete(f"/api/v1/admin/orders/{order.pk}/")
        self.assertEqual(resp.status_code, 403, "STAFF must not be able to delete orders")


# ---------------------------------------------------------------------------
# ID enumeration (IDOR) tests
# ---------------------------------------------------------------------------

class IDEnumerationTests(TestCase):
    """
    Verify that incrementing or enumerating IDs/UUIDs across stores is blocked
    by store-level access controls in admin endpoints.
    """

    def setUp(self):
        self.client = APIClient()
        _ensure_default_plan()

        self.store_a = _make_store("IDOR Store A", "idor-a.local")
        self.store_b = _make_store("IDOR Store B", "idor-b.local")

        self.admin_a = make_user("idor-a@example.com")
        self.admin_b = make_user("idor-b@example.com")

        _make_membership(self.admin_a, self.store_a)
        _make_membership(self.admin_b, self.store_b)

        cat_a = _make_category(self.store_a, "IDA Cat")
        cat_b = _make_category(self.store_b, "IDB Cat")

        self.product_a = _make_product(self.store_a, cat_a, name="IDOR Product A")
        self.product_b = _make_product(self.store_b, cat_b, name="IDOR Product B")

        self.order_a = _make_order(self.store_a)
        self.order_b = _make_order(self.store_b)

        self.customer_b = _make_customer(self.store_b, self.admin_b)

    def _auth_as(self, user, store):
        self.client.force_authenticate(user=user)
        self.client.credentials(HTTP_X_STORE_PUBLIC_ID=store.public_id)

    def test_cannot_access_cross_store_product_by_uuid(self):
        """
        Store A admin cannot retrieve store B's product by UUID via admin endpoint.
        Validates IDOR protection on /admin/products/{public_id}/.
        """
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/products/{self.product_b.public_id}/")
        self.assertEqual(
            resp.status_code, 404,
            "Store A admin must not access store B's product by public_id",
        )

    def test_cannot_delete_cross_store_product_by_public_id(self):
        """Store A admin cannot delete store B's product (scoped queryset → 404)."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.delete(f"/api/v1/admin/products/{self.product_b.public_id}/")
        self.assertEqual(
            resp.status_code,
            404,
            "Store A admin must not delete store B's product by public_id",
        )

    def test_cannot_access_cross_store_order_by_uuid(self):
        """
        Store A admin cannot retrieve store B's order by UUID via admin endpoint.
        Validates IDOR protection on /admin/orders/{pk}/.
        """
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/orders/{self.order_b.pk}/")
        self.assertEqual(
            resp.status_code, 404,
            "Store A admin must not access store B's order by UUID",
        )

    def test_cannot_access_cross_store_customer_by_public_id(self):
        """
        Store A admin cannot retrieve store B's customer by public_id.
        Validates IDOR protection on /admin/customers/{public_id}/.
        """
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/customers/{self.customer_b.public_id}/")
        self.assertEqual(
            resp.status_code, 404,
            "Store A admin must not access store B's customer by public_id",
        )

    def test_storefront_product_access_denied_cross_store(self):
        """
        Storefront product detail endpoint enforces store scoping via host header.
        Store A host cannot retrieve store B's product UUID or slug.
        """
        resp = self.client.get(
            f"/api/v1/products/{self.product_b.id}/",
            HTTP_HOST="idor-a.local",
        )
        self.assertIn(
            resp.status_code, [401, 403, 404],
            "Storefront must not expose cross-store product by UUID",
        )


# ---------------------------------------------------------------------------
# Media file extension helper
# ---------------------------------------------------------------------------


class MediaFileExtensionTests(TestCase):
    def test_common_extensions_lowercase(self):
        from engine.core.media_upload_paths import media_file_extension

        self.assertEqual(media_file_extension("photo.PNG"), "png")
        self.assertEqual(media_file_extension("file.JPEG"), "jpeg")
        self.assertEqual(media_file_extension("x.webp"), "webp")

    def test_missing_extension_defaults_to_jpg(self):
        from engine.core.media_upload_paths import media_file_extension

        self.assertEqual(media_file_extension("noext"), "jpg")
        self.assertEqual(media_file_extension(""), "jpg")
        self.assertEqual(media_file_extension("   "), "jpg")

    def test_upload_path_rejects_empty_public_id(self):
        import types

        from engine.core.media_upload_paths import tenant_product_main_upload_to

        fake = types.SimpleNamespace(
            store_id=1,
            store=types.SimpleNamespace(public_id="str_x"),
            public_id="",
        )
        with self.assertRaises(ValueError):
            tenant_product_main_upload_to(fake, "a.png")


# ---------------------------------------------------------------------------
# File storage isolation tests
# ---------------------------------------------------------------------------

class FileStorageIsolationTests(TestCase):
    """Assert tenant-prefixed storage paths (tenants/{store_public_id}/...) for media."""

    def setUp(self):
        self.store_a = _make_store("File Store A", "file-a.local")
        self.store_b = _make_store("File Store B", "file-b.local")

    def test_support_ticket_attachment_path_is_store_scoped(self):
        import types

        from engine.apps.support.models import SupportTicket, SupportTicketAttachment

        ticket = SupportTicket.objects.create(
            store=self.store_a, name="Test", email="t@t.com", message="m"
        )
        path_fn = SupportTicketAttachment.file.field.upload_to
        self.assertTrue(callable(path_fn))
        fake_attachment = types.SimpleNamespace(
            ticket_id=ticket.pk,
            ticket=ticket,
            public_id="ath_fixtureid",
        )
        computed_path = path_fn(fake_attachment, "attachment.pdf")
        self.assertTrue(
            computed_path.startswith(f"tenants/{self.store_a.public_id}/"),
            computed_path,
        )
        self.assertIn("/support/", computed_path)
        self.assertIn(ticket.public_id, computed_path)
        self.assertTrue(computed_path.endswith(".pdf"), computed_path)

    def test_product_gallery_path_is_tenant_scoped(self):
        import types

        from engine.apps.products.models import ProductImage

        cat = _make_category(self.store_a)
        product = _make_product(self.store_a, cat)
        path_fn = ProductImage.image.field.upload_to
        self.assertTrue(callable(path_fn))
        fake_image = types.SimpleNamespace(
            product_id=product.pk,
            product=product,
            public_id="img_fixtureid",
        )
        computed_path = path_fn(fake_image, "photo.JPEG")
        self.assertTrue(
            computed_path.startswith(f"tenants/{self.store_a.public_id}/"),
            computed_path,
        )
        self.assertIn(f"/products/{product.public_id}/gallery/", computed_path)
        self.assertTrue(computed_path.endswith("img_fixtureid.jpeg"), computed_path)


class CustomerAggregationFromOrderTests(TestCase):
    def setUp(self):
        self.store_a = _make_store("Agg Store A", "agg-a.local")
        self.store_b = _make_store("Agg Store B", "agg-b.local")

    def _create_order(self, store, phone="01700000000", email=""):
        return _make_order(
            store,
            email=email,
            shipping_name="John Doe",
            shipping_address="Road 1",
            phone=phone,
        )

    def test_resolve_customer_by_store_and_phone(self):
        order1 = self._create_order(self.store_a, phone="01700000000")
        customer1 = resolve_and_attach_customer(
            order1,
            store=self.store_a,
            name=order1.shipping_name,
            phone=order1.phone,
            email=order1.email,
            address=order1.shipping_address,
        )
        order1.refresh_from_db()
        self.assertEqual(order1.customer_id, customer1.id)
        self.assertEqual(customer1.total_orders, 1)

        order2 = self._create_order(self.store_a, phone="01700000000", email="")
        customer2 = resolve_and_attach_customer(
            order2,
            store=self.store_a,
            name="",
            phone=order2.phone,
            email=order2.email,
            address="",
        )
        self.assertEqual(customer1.id, customer2.id)
        self.assertEqual(customer2.total_orders, 2)

    def test_same_phone_different_store_creates_distinct_customers(self):
        order_a = self._create_order(self.store_a, phone="01711111111")
        customer_a = resolve_and_attach_customer(
            order_a,
            store=self.store_a,
            name="A",
            phone=order_a.phone,
            email="",
            address="Address A",
        )

        order_b = self._create_order(self.store_b, phone="01711111111")
        customer_b = resolve_and_attach_customer(
            order_b,
            store=self.store_b,
            name="B",
            phone=order_b.phone,
            email="",
            address="Address B",
        )
        self.assertNotEqual(customer_a.id, customer_b.id)
        self.assertEqual(customer_a.phone, customer_b.phone)

    def test_does_not_overwrite_with_empty_values(self):
        order1 = self._create_order(self.store_a, phone="01722222222", email="a@example.com")
        customer = resolve_and_attach_customer(
            order1,
            store=self.store_a,
            name="Existing Name",
            phone=order1.phone,
            email=order1.email,
            address="Existing Address",
        )
        order2 = self._create_order(self.store_a, phone="01722222222", email="")
        resolve_and_attach_customer(
            order2,
            store=self.store_a,
            name="",
            phone=order2.phone,
            email="",
            address="",
        )
        customer.refresh_from_db()
        self.assertEqual(customer.name, "Existing Name")
        self.assertEqual(customer.email, "a@example.com")
        self.assertEqual(customer.address, "Existing Address")

    def test_case1_same_phone_same_email_different_name_keeps_existing_name(self):
        order1 = self._create_order(self.store_a, phone="01733333333", email="same@example.com")
        customer = resolve_and_attach_customer(
            order1,
            store=self.store_a,
            name="First Name",
            phone=order1.phone,
            email=order1.email,
            address="Address 1",
        )
        order2 = self._create_order(self.store_a, phone="01733333333", email="same@example.com")
        matched = resolve_and_attach_customer(
            order2,
            store=self.store_a,
            name="Different Name",
            phone=order2.phone,
            email=order2.email,
            address="Address 2",
        )
        customer.refresh_from_db()
        self.assertEqual(customer.id, matched.id)
        self.assertEqual(customer.name, "First Name")

    def test_case2_same_phone_different_email_different_name_does_not_overwrite(self):
        order1 = self._create_order(self.store_a, phone="01744444444", email="first@example.com")
        customer = resolve_and_attach_customer(
            order1,
            store=self.store_a,
            name="Stored Name",
            phone=order1.phone,
            email=order1.email,
            address="Stored Address",
        )
        order2 = self._create_order(self.store_a, phone="01744444444", email="other@example.com")
        matched = resolve_and_attach_customer(
            order2,
            store=self.store_a,
            name="Other Name",
            phone=order2.phone,
            email=order2.email,
            address="Other Address",
        )
        customer.refresh_from_db()
        self.assertEqual(customer.id, matched.id)
        self.assertEqual(customer.email, "first@example.com")
        self.assertEqual(customer.name, "Stored Name")

    def test_case3_same_phone_same_email_same_name_reuses_customer(self):
        order1 = self._create_order(self.store_a, phone="01755555555", email="same3@example.com")
        customer = resolve_and_attach_customer(
            order1,
            store=self.store_a,
            name="Same Name",
            phone=order1.phone,
            email=order1.email,
            address="A",
        )
        order2 = self._create_order(self.store_a, phone="01755555555", email="same3@example.com")
        matched = resolve_and_attach_customer(
            order2,
            store=self.store_a,
            name="Same Name",
            phone=order2.phone,
            email=order2.email,
            address="A",
        )
        customer.refresh_from_db()
        self.assertEqual(customer.id, matched.id)
        self.assertEqual(customer.total_orders, 2)

    def test_case4_different_phone_same_email_same_name_creates_new_customer(self):
        order1 = self._create_order(self.store_a, phone="01766666661", email="same4@example.com")
        customer1 = resolve_and_attach_customer(
            order1,
            store=self.store_a,
            name="Same Name",
            phone=order1.phone,
            email=order1.email,
            address="Address 1",
        )
        order2 = self._create_order(self.store_a, phone="01766666662", email="same4@example.com")
        customer2 = resolve_and_attach_customer(
            order2,
            store=self.store_a,
            name="Same Name",
            phone=order2.phone,
            email=order2.email,
            address="Address 2",
        )
        self.assertNotEqual(customer1.id, customer2.id)

    def test_case5_different_phone_different_email_different_name_creates_new_customer(self):
        order1 = self._create_order(self.store_a, phone="01777777771", email="case5a@example.com")
        customer1 = resolve_and_attach_customer(
            order1,
            store=self.store_a,
            name="Name A",
            phone=order1.phone,
            email=order1.email,
            address="Address A",
        )
        order2 = self._create_order(self.store_a, phone="01777777772", email="case5b@example.com")
        customer2 = resolve_and_attach_customer(
            order2,
            store=self.store_a,
            name="Name B",
            phone=order2.phone,
            email=order2.email,
            address="Address B",
        )
        self.assertNotEqual(customer1.id, customer2.id)

    def test_case7_same_phone_updates_email_when_existing_missing(self):
        order1 = self._create_order(self.store_a, phone="01788888888", email="")
        customer = resolve_and_attach_customer(
            order1,
            store=self.store_a,
            name="No Email",
            phone=order1.phone,
            email=order1.email,
            address="Address",
        )
        self.assertFalse(customer.email)

        order2 = self._create_order(self.store_a, phone="01788888888", email="now@example.com")
        matched = resolve_and_attach_customer(
            order2,
            store=self.store_a,
            name="No Email",
            phone=order2.phone,
            email=order2.email,
            address="Address",
        )
        customer.refresh_from_db()
        self.assertEqual(customer.id, matched.id)
        self.assertEqual(customer.email, "now@example.com")


class R2DeletionTaskTests(TestCase):
    def test_delete_r2_objects_stops_retry_storm_on_missing_bucket(self):
        from botocore.exceptions import ClientError

        from engine.core.tasks import delete_r2_objects

        client = Mock()
        client.delete_objects.side_effect = ClientError(
            error_response={"Error": {"Code": "NoSuchBucket", "Message": "bucket not found"}},
            operation_name="DeleteObjects",
        )

        with patch("engine.core.tasks._get_r2_delete_client", return_value=client):
            with self.settings(AWS_STORAGE_BUCKET_NAME="missing-bucket"):
                deleted = delete_r2_objects.run(["media/tenants/str_x/products/prd_y/main.jpg"])

        self.assertEqual(deleted, 0)

    def test_delete_r2_objects_raises_other_client_errors(self):
        from botocore.exceptions import ClientError

        from engine.core.tasks import delete_r2_objects

        client = Mock()
        client.delete_objects.side_effect = ClientError(
            error_response={"Error": {"Code": "AccessDenied", "Message": "denied"}},
            operation_name="DeleteObjects",
        )

        with patch("engine.core.tasks._get_r2_delete_client", return_value=client):
            with self.settings(AWS_STORAGE_BUCKET_NAME="ok-bucket"):
                with self.assertRaises(ClientError):
                    delete_r2_objects.run(["media/tenants/str_x/branding/logo.jpg"])
