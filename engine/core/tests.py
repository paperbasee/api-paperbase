from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase, RequestFactory
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient

from engine.apps.stores.models import Store, StoreMembership
from engine.core.tenancy import resolve_store_from_host, get_active_store
from engine.core.ids import generate_public_id
from engine.apps.support.models import SupportTicket
from engine.apps.products.models import Product, Category
from engine.apps.cart.models import Cart, CartItem
from engine.apps.customers.models import Customer, CustomerAddress

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_user(email, password="pass1234", **kwargs):
    """Create a user using only email (no username)."""
    return User.objects.create_user(email=email, password=password, **kwargs)


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

class TenancyTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.client = APIClient()
        self.store = Store.objects.create(
            name="Test Store",
            domain="teststore.local",
            owner_name="Test Owner",
            owner_email="owner@example.com",
        )
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
        request = self.factory.get("/", HTTP_X_STORE_ID=self.store.public_id)
        request.user = self.user
        ctx = get_active_store(request)
        self.assertIsNotNone(ctx.store)
        self.assertEqual(ctx.store.id, self.store.id)
        self.assertIsNotNone(ctx.membership)

    def test_get_active_store_from_header_backward_compat_int(self):
        """Backward compat: integer store ID in header still resolves during migration window."""
        request = self.factory.get("/", HTTP_X_STORE_ID=str(self.store.id))
        request.user = self.user
        ctx = get_active_store(request)
        self.assertIsNotNone(ctx.store)
        self.assertEqual(ctx.store.id, self.store.id)


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

class AuthStoreEndpointsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = Store.objects.create(
            name="Test Store",
            domain="teststore.local",
            owner_name="Test Owner",
            owner_email="owner@example.com",
        )
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
            {"store_id": self.store.public_id},
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
        self.store = Store.objects.create(
            name="Tenant Store",
            domain="tenant.local",
            owner_name="Tenant Owner",
            owner_email="owner2@example.com",
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
        self.store = Store.objects.create(
            name="API Test Store",
            domain="apitest.local",
            owner_name="API Owner",
            owner_email="apiowner@example.com",
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
        active_store_id = resp_data.get("active_store_id")
        self.assertIsNotNone(active_store_id)
        self.assertFalse(
            str(active_store_id).isdigit(),
            f"active_store_id must be a public_id string, not integer: {active_store_id}",
        )
        self.assertTrue(str(active_store_id).startswith("str_"))

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
            store_id = s.get("id")
            self.assertFalse(
                str(store_id).isdigit(),
                f"stores[].id must be public_id, not integer: {store_id}",
            )
            self.assertTrue(str(store_id).startswith("str_"))

    def test_store_api_exposes_public_id(self):
        self._authenticate()
        resp = self.client.get(
            "/api/v1/admin/branding/",
            HTTP_X_STORE_ID=self.store.public_id,
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
            {"product_id": str(product.id), "quantity": 1},
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
            {"store_id": self.store.public_id},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)
        self.assertTrue(str(resp.data.get("active_store_id", "")).startswith("str_"))


# ---------------------------------------------------------------------------
# Password reset & change tests
# ---------------------------------------------------------------------------

class PasswordManagementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = make_user("pw_user@example.com", password="OldPass1234!")

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
        resp = self.client.post(
            "/api/v1/auth/password/reset/",
            {"email": "doesnotexist@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_password_reset_request_for_existing_user(self):
        resp = self.client.post(
            "/api/v1/auth/password/reset/",
            {"email": "pw_user@example.com"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

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
        self.user = make_user("verify@example.com")

    def _authenticate(self):
        resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": "verify@example.com", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")

    def test_new_user_is_not_verified(self):
        self.assertFalse(self.user.is_verified)

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

    def test_email_verify_rejects_invalid_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        resp = self.client.post(
            "/api/v1/auth/email/verify/",
            {"uid": uid, "token": "bad-token"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_resend_verification_for_unverified_user(self):
        self._authenticate()
        resp = self.client.post("/api/v1/auth/email/resend-verification/", format="json")
        self.assertEqual(resp.status_code, 200)

    def test_resend_verification_rejected_if_already_verified(self):
        self.user.is_verified = True
        self.user.save()
        self._authenticate()
        resp = self.client.post("/api/v1/auth/email/resend-verification/", format="json")
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# IDOR security tests
# ---------------------------------------------------------------------------

class IdrSecurityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = Store.objects.create(
            name="Security Store",
            domain="sec.local",
            owner_name="Sec Owner",
            owner_email="sec@example.com",
        )
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
