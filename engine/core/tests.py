from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory
from rest_framework.test import APIClient

from engine.apps.stores.models import Store, StoreMembership
from engine.core.tenancy import resolve_store_from_host, get_active_store
from engine.apps.support.models import SupportTicket
from engine.apps.products.models import Product, NavbarCategory, Category

User = get_user_model()


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
        self.user = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="pass1234",
        )
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

    def test_get_active_store_from_header(self):
        request = self.factory.get("/", HTTP_X_STORE_ID=str(self.store.id))
        request.user = self.user
        ctx = get_active_store(request)
        self.assertIsNotNone(ctx.store)
        self.assertEqual(ctx.store.id, self.store.id)
        self.assertIsNotNone(ctx.membership)


class AuthStoreEndpointsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = Store.objects.create(
            name="Test Store",
            domain="teststore.local",
            owner_name="Test Owner",
            owner_email="owner@example.com",
        )
        self.user = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="pass1234",
        )
        StoreMembership.objects.create(
            user=self.user,
            store=self.store,
            role=StoreMembership.Role.OWNER,
        )

    def authenticate(self):
        response = self.client.post(
            "/api/v1/auth/token/",
            {"username": "owner", "password": "pass1234"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        token = response.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_me_returns_memberships(self):
        self.authenticate()
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.user.id)
        self.assertGreaterEqual(len(response.data["stores"]), 1)

    def test_switch_store_issues_tokens_without_password(self):
        self.authenticate()
        response = self.client.post("/api/v1/auth/switch-store/", {"store_id": self.store.id}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)


class SupportTicketTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = Store.objects.create(
            name="Tenant Store",
            domain="tenant.local",
            owner_name="Tenant Owner",
            owner_email="owner2@example.com",
        )

        self.owner = User.objects.create_user(
            username="owner2",
            email="owner2@example.com",
            password="pass1234",
        )
        StoreMembership.objects.create(
            user=self.owner,
            store=self.store,
            role=StoreMembership.Role.OWNER,
        )

    def _auth_owner(self):
        resp = self.client.post("/api/v1/auth/token/", {"username": "owner2", "password": "pass1234"}, format="json")
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
        self.assertTrue(SupportTicket.objects.filter(store=self.store, email="guest@example.com").exists())

    def test_store_staff_can_list_tickets_via_admin(self):
        SupportTicket.objects.create(store=self.store, name="G", email="g@example.com", message="m")
        self._auth_owner()
        resp = self.client.get("/api/v1/admin/support-tickets/")
        self.assertEqual(resp.status_code, 200)


class ProductTenancyTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store_a = Store.objects.create(
            name="Store A",
            domain="a.local",
            owner_name="Owner A",
            owner_email="owner@a.local",
        )
        self.store_b = Store.objects.create(
            name="Store B",
            domain="b.local",
            owner_name="Owner B",
            owner_email="owner@b.local",
        )

        self.nav_a = NavbarCategory.objects.create(store=self.store_a, name="Gadgets A", slug="gadgets")
        self.nav_b = NavbarCategory.objects.create(store=self.store_b, name="Gadgets B", slug="gadgets")

        self.cat_a = Category.objects.create(
            store=self.store_a,
            navbar_category=self.nav_a,
            name="Audio",
            slug="audio",
        )
        self.cat_b = Category.objects.create(
            store=self.store_b,
            navbar_category=self.nav_b,
            name="Audio",
            slug="audio",
        )

        self.prod_a = Product.objects.create(
            store=self.store_a,
            name="Product A",
            brand="Brand",
            price=10,
            category=self.nav_a,
            sub_category=self.cat_a,
            stock=5,
        )
        self.prod_b = Product.objects.create(
            store=self.store_b,
            name="Product B",
            brand="Brand",
            price=20,
            category=self.nav_b,
            sub_category=self.cat_b,
            stock=5,
        )

    def test_product_list_is_store_scoped(self):
        resp_a = self.client.get("/api/v1/products/", HTTP_HOST="a.local")
        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(len(resp_a.data["results"]), 1)
        self.assertEqual(resp_a.data["results"][0]["name"], "Product A")

        resp_b = self.client.get("/api/v1/products/", HTTP_HOST="b.local")
        self.assertEqual(resp_b.status_code, 200)
        self.assertEqual(len(resp_b.data["results"]), 1)
        self.assertEqual(resp_b.data["results"][0]["name"], "Product B")

