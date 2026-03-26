import uuid as _uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase, RequestFactory
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from engine.apps.stores.models import Domain, Store, StoreMembership
from engine.core.tenancy import resolve_store_from_host, get_active_store
from engine.core.ids import generate_public_id
from engine.core.models import ActivityLog
from engine.apps.support.models import SupportTicket
from engine.apps.products.models import Product, Category
from engine.apps.orders.models import Order, OrderItem
from engine.apps.shipping.models import ShippingZone
from engine.apps.orders.services import resolve_and_attach_customer
from engine.apps.customers.models import Customer, CustomerAddress
from engine.apps.coupons.models import Coupon
from engine.apps.cart.models import Cart, CartItem
from engine.apps.wishlist.models import WishlistItem
from engine.apps.reviews.models import Review
from engine.apps.notifications.models import StorefrontCTA

User = get_user_model()

PASSWORD_RESET_OK_MESSAGE = (
    "If an account exists, we've sent a password reset link."
)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _make_store(name, domain, owner_email=None):
    email = owner_email or f"owner@{domain}"
    store = Store.objects.create(
        name=name,
        domain=None,
        owner_name=f"{name} Owner",
        owner_email=email,
    )
    if domain:
        Domain.objects.filter(store=store, is_custom=False).update(
            domain=domain.strip().lower().split(":", 1)[0]
        )
    return store


def _make_membership(user, store, role=StoreMembership.Role.OWNER):
    return StoreMembership.objects.create(user=user, store=store, role=role)


def _make_category(store, name="Cat", slug=None):
    return Category.objects.create(
        store=store, name=name, slug=slug or name.lower().replace(" ", "-"),
    )


def _make_product(store, category, name="Product", price=10, stock=5):
    return Product.objects.create(
        store=store, category=category, name=name, price=price, stock=stock,
        status=Product.Status.ACTIVE, is_active=True,
    )


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


def _make_customer(store, user):
    return Customer.objects.create(
        store=store,
        user=user,
        name=(user.email if user else ""),
        phone=f"u{user.pk}" if user else f"s{store.pk}",
        email=(user.email if user else None),
    )


def _make_coupon(store, code=None):
    code = code or f"SAVE-{_uuid.uuid4().hex[:6].upper()}"
    return Coupon.objects.create(store=store, code=code, discount_type="percentage", discount_value=10)


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

    def test_resolve_store_from_host(self):
        request = self.factory.get("/", HTTP_HOST="teststore.local")
        store = resolve_store_from_host(request)
        self.assertIsNotNone(store)
        self.assertEqual(store.id, self.store.id)

    def test_platform_host_does_not_resolve_store(self):
        request = self.factory.get("/", HTTP_HOST="localhost")
        store = resolve_store_from_host(request)
        self.assertIsNone(store)

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
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("detail"), "Unknown tenant host.")

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

    def test_generate_public_id_domain_prefix(self):
        pid = generate_public_id("domain")
        self.assertTrue(pid.startswith("dom_"), pid)
        self.assertEqual(len(pid), 24)

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
            ("cart", "crt_"),
            ("cartitem", "cit_"),
            ("coupon", "cpn_"),
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
            owner_name="Test Owner",
            owner_email="owner@test.com",
        )
        self.assertIsNotNone(store.public_id)
        self.assertTrue(store.public_id.startswith("str_"))

    def test_public_id_is_immutable(self):
        store = Store.objects.create(
            name="Immutable Store",
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

    def test_cart_item_exposes_public_id(self):
        cat = Category.objects.create(
            store=self.store,
            name="Test Cat",
            slug="test-cat",
        )
        product = Product.objects.create(
            store=self.store,
            name="Test Product",
            price=10,
            category=cat,
            stock=5,
        )
        resp = self.client.post(
            "/api/v1/cart/add/",
            {"product_public_id": product.public_id, "quantity": 1},
            format="json",
            HTTP_HOST="apitest.local",
        )
        self.assertEqual(resp.status_code, 201)
        item_public_id = resp.data.get("public_id")
        self.assertIsNotNone(item_public_id)
        self.assertTrue(str(item_public_id).startswith("cit_"))

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


# ---------------------------------------------------------------------------
# Email verification tests
# ---------------------------------------------------------------------------

class EmailVerificationTests(TestCase):
    def setUp(self):
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
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.data.get("code"), "email_not_verified")
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
        self.assertEqual(resp.data.get("detail"), "Email verification is required.")

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

    def test_cart_item_update_requires_ownership(self):
        """User B cannot update User A's cart item using a public_id."""
        cart_a = Cart.objects.create(user=self.user_a)
        cat = Category.objects.create(store=self.store, name="Cat", slug="cat")
        product = Product.objects.create(
            store=self.store, name="Prod", price=10, category=cat, stock=5
        )
        item_a = CartItem.objects.create(cart=cart_a, product=product, quantity=1)

        self._auth_as(self.user_b)
        resp = self.client.patch(
            f"/api/v1/cart/items/{item_a.public_id}/update/",
            {"quantity": 99},
            format="json",
        )
        self.assertIn(
            resp.status_code,
            [404, 403],
            "User B should not be able to modify User A's cart item",
        )

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
        self.assertEqual(resp.status_code, 201, resp.data)
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

        self.cat_a = _make_category(self.store_a, "CatA", "cat-a")
        self.cat_b = _make_category(self.store_b, "CatB", "cat-b")

        self.product_a = _make_product(self.store_a, self.cat_a, name="Product A")
        self.product_b = _make_product(self.store_b, self.cat_b, name="Product B")
        self.zone_a = _default_shipping_zone(self.store_a)

        self.order_a = _make_order(self.store_a, "cust-a@example.com")
        self.order_b = _make_order(self.store_b, "cust-b@example.com")
        self.shared_order_a = _make_order(self.store_a, "shared@example.com", user=self.shared_user)
        self.shared_order_b = _make_order(self.store_b, "shared@example.com", user=self.shared_user)

        self.customer_a = _make_customer(self.store_a, self.admin_a)
        self.customer_b = _make_customer(self.store_b, self.admin_b)

        self.ticket_a = SupportTicket.objects.create(
            store=self.store_a, name="A", email="a@example.com", message="help"
        )
        self.ticket_b = SupportTicket.objects.create(
            store=self.store_b, name="B", email="b@example.com", message="help"
        )

        self.coupon_a = _make_coupon(self.store_a)
        self.coupon_b = _make_coupon(self.store_b)

        self.notif_a = StorefrontCTA.objects.create(
            store=self.store_a, cta_text="CTA Store A", is_active=True
        )
        self.notif_b = StorefrontCTA.objects.create(
            store=self.store_b, cta_text="CTA Store B", is_active=True
        )

        self.cart_item_b = CartItem.objects.create(
            cart=Cart.objects.create(user=self.shared_user),
            product=self.product_b,
            quantity=1,
        )
        self.wishlist_item_b = WishlistItem.objects.create(
            user=self.shared_user,
            product=self.product_b,
        )
        self.review_b = Review.objects.create(
            product=self.product_b,
            user=self.shared_user,
            rating=5,
            title="Great",
            body="Great product",
            status=Review.Status.APPROVED,
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
        self.assertEqual(resp.status_code, 404)

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

    def test_admin_order_detail_cross_store_denied(self):
        """Store A admin fetching store B's order UUID returns 404."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get(f"/api/v1/admin/orders/{self.order_b.pk}/")
        self.assertEqual(resp.status_code, 404)

    def test_storefront_order_detail_isolated_by_store(self):
        """Store A tenant host must not fetch Store B guest order by public_id/email."""
        resp = self.client.get(
            f"/api/v1/orders/{self.order_b.public_id}/",
            {"email": "cust-b@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(resp.status_code, 404)

    def test_storefront_order_list_isolated_by_active_store(self):
        """Shared user with memberships in both stores must only see active-store orders."""
        self.client.force_authenticate(user=self.shared_user)
        resp = self.client.get("/api/v1/orders/my/", HTTP_X_STORE_PUBLIC_ID=self.store_a.public_id)
        self.assertEqual(resp.status_code, 200)
        public_ids = [item.get("public_id") for item in resp.data.get("results", resp.data)]
        self.assertIn(self.shared_order_a.public_id, public_ids)
        self.assertNotIn(self.shared_order_b.public_id, public_ids)

    def test_cart_add_cross_store_product_is_blocked(self):
        resp = self.client.post(
            "/api/v1/cart/add/",
            {"product_public_id": self.product_b.public_id, "quantity": 1},
            format="json",
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(resp.status_code, 404)

    def test_wishlist_add_cross_store_product_is_blocked(self):
        resp = self.client.post(
            "/api/v1/wishlist/add/",
            {"product_public_id": self.product_b.public_id},
            format="json",
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(resp.status_code, 404)

    def test_review_summary_cross_store_product_is_blocked(self):
        resp = self.client.get(
            "/api/v1/reviews/summary/",
            {"product_public_id": self.product_b.public_id},
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(resp.status_code, 404)

    def test_review_create_cross_store_product_is_blocked(self):
        self.client.force_authenticate(user=self.shared_user)
        resp = self.client.post(
            "/api/v1/reviews/create/",
            {
                "product": self.product_b.public_id,
                "rating": 5,
                "title": "Blocked",
                "body": "Should fail",
            },
            format="json",
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(resp.status_code, 400)

    def test_cross_store_order_access(self):
        """Store A context must not access Store B order detail by public_id."""
        resp = self.client.get(
            f"/api/v1/orders/{self.order_b.public_id}/",
            {"email": "cust-b@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(resp.status_code, 404)

    def test_cross_store_product_access(self):
        """Store A context must not access Store B product by public_id or slug."""
        by_public_id = self.client.get(
            f"/api/v1/products/{self.product_b.public_id}/",
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(by_public_id.status_code, 404)

        by_slug = self.client.get(
            f"/api/v1/products/{self.product_b.slug}/",
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(by_slug.status_code, 404)

    def test_cross_store_cart_access(self):
        """Cart add/remove with Store B product from Store A context must fail."""
        add_resp = self.client.post(
            "/api/v1/cart/add/",
            {"product_public_id": self.product_b.public_id, "quantity": 1},
            format="json",
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(add_resp.status_code, 404)

        remove_resp = self.client.post(
            f"/api/v1/cart/remove-by-product/{self.product_b.public_id}/",
            format="json",
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(remove_resp.status_code, 404)

    def test_cross_store_wishlist_access(self):
        """Wishlist add/remove with Store B product from Store A context must fail."""
        add_resp = self.client.post(
            "/api/v1/wishlist/add/",
            {"product_public_id": self.product_b.public_id},
            format="json",
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(add_resp.status_code, 404)

        remove_resp = self.client.post(
            f"/api/v1/wishlist/remove/{self.product_b.public_id}/",
            format="json",
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(remove_resp.status_code, 404)

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
        self.assertEqual(by_internal_id.status_code, 404)

        # A valid response must still expose only public identifiers.
        valid = self.client.get(
            f"/api/v1/orders/{self.order_a.public_id}/",
            {"email": "cust-a@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(valid.status_code, 200, valid.data)
        self.assertIn("public_id", valid.data)
        self.assertNotIn("id", valid.data)
        for item in valid.data.get("items", []):
            self.assertIn("public_id", item)
            self.assertNotIn("id", item)

    def test_order_list_store_isolation(self):
        """
        `/orders/my/` must return only active-store orders and no internal ids.
        """
        self.client.force_authenticate(user=self.shared_user)
        resp = self.client.get(
            "/api/v1/orders/my/",
            HTTP_X_STORE_PUBLIC_ID=self.store_a.public_id,
        )
        self.assertEqual(resp.status_code, 200)
        rows = resp.data.get("results", resp.data)
        ids = [row.get("public_id") for row in rows]
        self.assertIn(self.shared_order_a.public_id, ids)
        self.assertNotIn(self.shared_order_b.public_id, ids)
        for row in rows:
            self.assertIn("public_id", row)
            self.assertNotIn("id", row)

    def test_invalid_public_id_returns_404(self):
        """
        Order detail must return 404 for invalid ID, wrong store, and wrong identity.
        """
        invalid_resp = self.client.get(
            "/api/v1/orders/ord_not_real_123/",
            {"email": "cust-a@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(invalid_resp.status_code, 404)

        wrong_store_resp = self.client.get(
            f"/api/v1/orders/{self.order_b.public_id}/",
            {"email": "cust-b@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(wrong_store_resp.status_code, 404)

        # Guest lookup with wrong email must fail.
        wrong_email_resp = self.client.get(
            f"/api/v1/orders/{self.order_a.public_id}/",
            {"email": "wrong@example.com"},
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(wrong_email_resp.status_code, 404)

        # Authenticated user mismatch must fail.
        self.client.force_authenticate(user=self.admin_b)
        wrong_user_resp = self.client.get(
            f"/api/v1/orders/{self.shared_order_a.public_id}/",
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(wrong_user_resp.status_code, 404)

        # Authenticated owner succeeds in correct store.
        self.client.force_authenticate(user=self.shared_user)
        ok_resp = self.client.get(
            f"/api/v1/orders/{self.shared_order_a.public_id}/",
            HTTP_HOST="admin-a.local",
        )
        self.assertEqual(ok_resp.status_code, 200, ok_resp.data)

    def test_admin_order_create_rejects_cross_store_product(self):
        """Store A admin must not create order using Store B product public_id."""
        self._auth_as(self.admin_a, self.store_a)
        payload = {
            "shipping_name": "Cross Tenant",
            "phone": "01799999999",
            "email": "cross@example.com",
            "shipping_address": "Address",
            "district": "Dhaka",
            "shipping_zone": self.zone_a.public_id,
            "items": [
                {
                    "product": self.product_b.public_id,
                    "quantity": 1,
                    "price": "10.00",
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
                    "product": self.product_a.public_id,
                    "quantity": 1,
                    "price": "10.00",
                }
            ],
        }
        resp = self.client.post("/api/v1/admin/orders/", payload, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("shipping_zone", resp.data)

    def test_admin_order_create_with_shipping_zone_only_succeeds(self):
        """Admin order create accepts explicit shipping_zone without shipping_method."""
        self._auth_as(self.admin_a, self.store_a)
        payload = {
            "shipping_name": "Zone Only",
            "phone": "01799999999",
            "email": "zone-only@example.com",
            "shipping_address": "Address",
            "district": "Dhaka",
            "shipping_zone": self.zone_a.public_id,
            "items": [
                {
                    "product": self.product_a.public_id,
                    "quantity": 1,
                    "price": "10.00",
                }
            ],
        }
        resp = self.client.post("/api/v1/admin/orders/", payload, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)

    def test_admin_order_detail_handles_deleted_product_item(self):
        """Deleted product references in order items must serialize as unavailable."""
        order = _make_order(self.store_a, "deleted-product@example.com")
        OrderItem.objects.create(
            order=order,
            product=self.product_a,
            quantity=1,
            price="10.00",
        )
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
        self.assertEqual(resp.status_code, 404)

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
    # Coupon isolation
    # ------------------------------------------------------------------

    def test_admin_coupon_isolated_by_store(self):
        """Store A admin must not see store B's coupons."""
        self._auth_as(self.admin_a, self.store_a)
        resp = self.client.get("/api/v1/admin/coupons/")
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        public_ids = [item.get("public_id") or item.get("id") for item in results]
        self.assertIn(self.coupon_a.public_id, public_ids)
        self.assertNotIn(self.coupon_b.public_id, public_ids)

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
        cat_b = _make_category(self.store_b, "Cat B", "cat-b")
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
        cat_b = _make_category(self.store_b, "Cat B2", "cat-b2")
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

        self.cat = _make_category(self.store, "RoleCat", "role-cat")
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

    def test_admin_cannot_delete_products(self):
        """Store ADMIN role must not be able to delete products."""
        self._auth_as(self.admin_user)
        resp = self.client.delete(f"/api/v1/admin/products/{self.product.public_id}/")
        self.assertEqual(resp.status_code, 403, "Store ADMIN must not be able to delete products")

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

        cat_a = _make_category(self.store_a, "IDA Cat", "ida-cat")
        cat_b = _make_category(self.store_b, "IDB Cat", "idb-cat")

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
        self.assertEqual(
            resp.status_code, 404,
            "Storefront must not expose cross-store product by UUID",
        )


# ---------------------------------------------------------------------------
# File storage isolation tests
# ---------------------------------------------------------------------------

class FileStorageIsolationTests(TestCase):
    """
    Document and assert expected file path isolation for uploaded media.

    NOTE: Currently product/category images use flat paths (products/, categories/)
    shared across all tenants, making paths guessable across stores. Only support
    ticket attachments use per-store isolation (store_{id}/support/...).

    These tests document the isolation posture and will fail if the flat paths
    are inadvertently changed to cross-store paths.
    """

    def setUp(self):
        self.store_a = _make_store("File Store A", "file-a.local")
        self.store_b = _make_store("File Store B", "file-b.local")

    def test_support_ticket_attachment_path_is_store_scoped(self):
        """
        Support ticket attachment upload path must include the store ID,
        providing isolation between tenants.
        """
        from engine.apps.support.models import SupportTicketAttachment, SupportTicket
        ticket = SupportTicket.objects.create(
            store=self.store_a, name="Test", email="t@t.com", message="m"
        )
        path_fn = SupportTicketAttachment.file.field.upload_to
        # upload_to is a callable or string; resolve the expected path pattern.
        if callable(path_fn):
            # Simulate with a mock instance.
            import types
            fake_instance = types.SimpleNamespace(
                ticket=ticket,
                ticket_id=ticket.pk,
            )
            computed_path = path_fn(fake_instance, "attachment.pdf")
            self.assertIn(
                self.store_a.public_id,
                computed_path,
                "Support ticket attachment path must include store public_id for isolation",
            )
        else:
            # String upload_to — just check it contains 'support'
            self.assertIn("support", str(path_fn))

    def test_product_image_path_is_not_store_scoped(self):
        """
        Documents the Low-severity finding: product images use a flat 'products/'
        path with no per-store prefix. A URL is guessable across tenants.
        This test documents the current (insecure) posture and must be updated
        when per-store product image paths are implemented.
        """
        from engine.apps.products.models import ProductImage
        image_upload_to = ProductImage.image.field.upload_to
        path_str = image_upload_to if isinstance(image_upload_to, str) else "products/"
        # Currently this path is flat — does NOT include store context.
        self.assertNotIn(
            "store_",
            path_str,
            "KNOWN LOW SEVERITY: product image paths are not store-scoped. "
            "Update this test when per-store paths are implemented.",
        )


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
