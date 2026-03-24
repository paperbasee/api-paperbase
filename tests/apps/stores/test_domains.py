"""Domain model, HTTP tenant routing, API guard, and WebSocket isolation."""

import asyncio
import json
import secrets
from unittest.mock import patch

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.conf import settings
from django.core.cache import caches
from django.db import connection
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from config.asgi import application
from engine.apps.orders.models import Order
from engine.apps.shipping.models import ShippingZone
from engine.apps.products.models import Category, Product
from engine.apps.stores.domain_serializers import CustomDomainCreateSerializer
from engine.apps.stores.models import Domain, Store, StoreMembership
from engine.apps.stores.services import provision_generated_domain
from engine.core.realtime import emit_store_event
from engine.core.domain_resolution_cache import get_domain_resolution_payload
from engine.core.tenancy import TenantApiGuardMiddleware, TenantResolutionMiddleware
from engine.core.ws_domain import DomainWebSocketMiddleware

User = get_user_model()

# Pinned tenant hosts (global unique Domain.domain)
HOST_STORE_A = "a123.mybaas.com"
HOST_STORE_B = "b456.mybaas.com"
HOST_STORE_A_CUSTOM = "api.storea.com"
HOST_UNVERIFIED = "unverified-tenant.example.com"
HOST_UNKNOWN = "totally-unknown.invalid"
HOST_WS_RATE_LIMIT = "ws-rate-limit.mybaas.com"
HOST_HTTP_RATE_LIMIT = "http-rate-limit.mybaas.com"


def _clear_tenant_resolution_cache() -> None:
    alias = getattr(settings, "TENANT_RESOLUTION_CACHE_ALIAS", "tenant_resolution")
    caches[alias].clear()


def _apply_tenant_middleware(request):
    TenantResolutionMiddleware(lambda r: r).process_request(request)
    return TenantApiGuardMiddleware(lambda r: r).process_request(request)


def make_store(name: str, owner_email_suffix: str = "example.com") -> Store:
    return Store.objects.create(
        name=name,
        domain=None,
        owner_name=f"{name} Owner",
        owner_email=f"owner@{owner_email_suffix}",
    )


def pin_generated_domain(store: Store, host: str, *, verified: bool = True) -> None:
    Domain.objects.filter(store=store, is_custom=False).update(
        domain=host,
        is_verified=verified,
    )


def make_user(email: str, password: str = "pass1234", **kwargs):
    return User.objects.create_user(email=email, password=password, **kwargs)


def make_membership(user, store, role=StoreMembership.Role.OWNER):
    return StoreMembership.objects.create(
        user=user, store=store, role=role, is_active=True
    )


def obtain_access_token(email: str, password: str, *, http_host: str = "localhost") -> str:
    client = APIClient()
    r = client.post(
        "/api/v1/auth/token/",
        {"email": email, "password": password},
        format="json",
        HTTP_HOST=http_host,
    )
    assert r.status_code == 200, r.content
    return r.data["access"]


def make_category(store, name="Cat", slug=None):
    return Category.objects.create(
        store=store,
        name=name,
        slug=slug or name.lower().replace(" ", "-"),
    )


def make_product(store, category, name="Product", brand="", price=10, stock=5):
    return Product.objects.create(
        store=store,
        category=category,
        name=name,
        brand=brand,
        price=price,
        stock=stock,
        status=Product.Status.ACTIVE,
        is_active=True,
    )


class DomainDashboardApiTestMixin:
    """Authenticated owner + APIClient on platform host (JWT + X-Store-ID)."""

    platform_host = "localhost"

    def setUp(self):
        _clear_tenant_resolution_cache()
        self.client = APIClient()
        self.store = Store.objects.create(
            name="D Store",
            domain=None,
            owner_name="O",
            owner_email="o@example.com",
        )
        self.user = User.objects.create_user(email="admin@example.com", password="pass1234")
        StoreMembership.objects.create(
            user=self.user,
            store=self.store,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        )
        resp = self.client.post(
            "/api/v1/auth/token/",
            {"email": "admin@example.com", "password": "pass1234"},
            format="json",
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(resp.status_code, 200)
        self.access = resp.data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")


class DomainApiTests(DomainDashboardApiTestMixin, TestCase):
    """Dashboard domain APIs run on platform host (JWT + X-Store-ID), not tenant Host."""

    def test_list_domains_includes_generated(self):
        r = self.client.get(
            "/api/v1/stores/domains/",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r.status_code, 200)
        payload = r.data
        rows = payload["results"] if isinstance(payload, dict) and "results" in payload else payload
        self.assertGreaterEqual(len(rows), 1)
        gen = next(x for x in rows if not x["is_custom"])
        self.assertTrue(gen["is_verified"])
        self.assertTrue(gen["public_id"].startswith("dom_"))

    def test_cannot_delete_generated_domain(self):
        gen = Domain.objects.get(store=self.store, is_custom=False)
        r = self.client.delete(
            f"/api/v1/stores/domains/{gen.public_id}/",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r.status_code, 400)

    def test_second_custom_domain_rejected(self):
        Domain.objects.create(
            store=self.store,
            domain="custom-one.example.com",
            is_custom=True,
            is_verified=False,
            is_primary=False,
            verification_token="abc",
        )
        r = self.client.post(
            "/api/v1/stores/domains/",
            {"domain": "custom-two.example.com"},
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r.status_code, 400)

    @patch("engine.apps.stores.domain_views.txt_record_contains_token")
    def test_verify_custom_domain(self, mock_txt):
        mock_txt.return_value = True
        dom = Domain.objects.create(
            store=self.store,
            domain="verify.example.com",
            is_custom=True,
            is_verified=False,
            is_primary=False,
            verification_token="tokensecret",
        )
        r = self.client.post(
            f"/api/v1/stores/domains/{dom.public_id}/verify/",
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r.status_code, 200)
        dom.refresh_from_db()
        self.assertTrue(dom.is_verified)

    @patch("engine.apps.stores.domain_views.txt_record_contains_token", return_value=True)
    def test_set_verified_custom_domain_primary_leaves_single_primary(self, _mock_txt):
        dom = Domain.objects.create(
            store=self.store,
            domain="primary-custom.example.com",
            is_custom=True,
            is_verified=False,
            is_primary=False,
            verification_token="tokpri",
        )
        self.assertEqual(
            self.client.post(
                f"/api/v1/stores/domains/{dom.public_id}/verify/",
                format="json",
                HTTP_X_STORE_ID=self.store.public_id,
                HTTP_HOST=self.platform_host,
            ).status_code,
            200,
        )
        r = self.client.post(
            f"/api/v1/stores/domains/{dom.public_id}/set-primary/",
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Domain.objects.filter(store=self.store, is_primary=True).count(), 1)
        dom.refresh_from_db()
        gen = Domain.objects.get(store=self.store, is_custom=False)
        self.assertTrue(dom.is_primary)
        self.assertFalse(gen.is_primary)

    @patch("engine.apps.stores.domain_views.txt_record_contains_token", return_value=True)
    def test_switch_primary_back_to_generated_domain(self, _mock_txt):
        dom = Domain.objects.create(
            store=self.store,
            domain="switch-back.example.com",
            is_custom=True,
            is_verified=False,
            is_primary=False,
            verification_token="toksw",
        )
        self.client.post(
            f"/api/v1/stores/domains/{dom.public_id}/verify/",
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.client.post(
            f"/api/v1/stores/domains/{dom.public_id}/set-primary/",
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        gen = Domain.objects.get(store=self.store, is_custom=False)
        r = self.client.post(
            f"/api/v1/stores/domains/{gen.public_id}/set-primary/",
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Domain.objects.filter(store=self.store, is_primary=True).count(), 1)
        gen.refresh_from_db()
        dom.refresh_from_db()
        self.assertTrue(gen.is_primary)
        self.assertFalse(dom.is_primary)

    @patch("engine.apps.stores.domain_views.txt_record_contains_token", return_value=True)
    def test_delete_primary_custom_domain_repromotes_generated(self, _mock_txt):
        dom = Domain.objects.create(
            store=self.store,
            domain="delete-primary.example.com",
            is_custom=True,
            is_verified=False,
            is_primary=False,
            verification_token="tokdel",
        )
        self.client.post(
            f"/api/v1/stores/domains/{dom.public_id}/verify/",
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.client.post(
            f"/api/v1/stores/domains/{dom.public_id}/set-primary/",
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        r = self.client.delete(
            f"/api/v1/stores/domains/{dom.public_id}/",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r.status_code, 204)
        gen = Domain.objects.get(store=self.store, is_custom=False)
        self.assertTrue(gen.is_primary)
        self.assertEqual(Domain.objects.filter(store=self.store, is_primary=True).count(), 1)
        dom_deleted = Domain.all_objects.get(pk=dom.pk)
        self.assertTrue(dom_deleted.is_deleted)
        self.assertIsNotNone(dom_deleted.deleted_at)

    @patch("engine.apps.stores.domain_views.txt_record_contains_token", return_value=False)
    def test_verify_custom_domain_dns_mismatch_stays_unverified(self, _mock_txt):
        dom = Domain.objects.create(
            store=self.store,
            domain="dns-bad.example.com",
            is_custom=True,
            is_verified=False,
            is_primary=False,
            verification_token="wrongtxt",
        )
        r = self.client.post(
            f"/api/v1/stores/domains/{dom.public_id}/verify/",
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r.status_code, 400)
        dom.refresh_from_db()
        self.assertFalse(dom.is_verified)

    @patch("engine.apps.stores.domain_views.txt_record_contains_token", return_value=False)
    def test_verify_custom_domain_dns_missing_treated_as_failure(self, _mock_txt):
        dom = Domain.objects.create(
            store=self.store,
            domain="dns-missing.example.com",
            is_custom=True,
            is_verified=False,
            is_primary=False,
            verification_token="missing",
        )
        r = self.client.post(
            f"/api/v1/stores/domains/{dom.public_id}/verify/",
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r.status_code, 400)
        dom.refresh_from_db()
        self.assertFalse(dom.is_verified)

    def test_add_custom_domain_via_api_stores_lowercase(self):
        r = self.client.post(
            "/api/v1/stores/domains/",
            {"domain": "API.StoreNorm.example.com"},
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r.status_code, 201)
        dom = Domain.objects.get(public_id=r.data["public_id"])
        self.assertEqual(dom.domain, "api.storenorm.example.com")

    def test_post_custom_domain_already_registered_elsewhere_returns_400(self):
        other = make_store("OtherDomainHolder")
        pin_generated_domain(other, "odh-gen.mybaas.com")
        Domain.objects.create(
            store=other,
            domain="global-taken.example.com",
            is_custom=True,
            is_verified=False,
            is_primary=False,
            verification_token="ot",
        )
        r = self.client.post(
            "/api/v1/stores/domains/",
            {"domain": "Global-Taken.example.com"},
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r.status_code, 400)


class TenantResolutionHttpTests(TestCase):
    """Domain → store resolution and API guard (HTTP)."""

    def setUp(self):
        _clear_tenant_resolution_cache()
        self.factory = RequestFactory()
        self.client = APIClient()
        self.store_a = make_store("Store A")
        self.store_b = make_store("Store B")
        pin_generated_domain(self.store_a, HOST_STORE_A)
        pin_generated_domain(self.store_b, HOST_STORE_B)
        Domain.objects.create(
            store=self.store_a,
            domain=HOST_STORE_A_CUSTOM,
            is_custom=True,
            is_verified=True,
            is_primary=False,
            verification_token=None,
        )
        self.store_unverified = make_store("Unverified Host Store")
        pin_generated_domain(self.store_unverified, HOST_UNVERIFIED, verified=False)

    def test_resolves_generated_domain_to_store(self):
        req = self.factory.get("/api/v1/products/", HTTP_HOST=HOST_STORE_A)
        TenantResolutionMiddleware(lambda r: r).process_request(req)
        self.assertFalse(req.is_platform_request)
        self.assertEqual(req.store, self.store_a)

    def test_unknown_tenant_host_products_forbidden(self):
        r = self.client.get("/api/v1/products/", HTTP_HOST=HOST_UNKNOWN)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json().get("detail"), "Unknown tenant host.")

    def test_unverified_domain_rejected_for_tenant_api(self):
        r = self.client.get("/api/v1/products/", HTTP_HOST=HOST_UNVERIFIED)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json().get("detail"), "Unknown tenant host.")

    def test_custom_verified_domain_resolves_to_store(self):
        req = self.factory.get("/", HTTP_HOST=HOST_STORE_A_CUSTOM)
        TenantResolutionMiddleware(lambda r: r).process_request(req)
        self.assertEqual(req.store, self.store_a)

    def test_custom_domain_storefront_products_ok(self):
        cat = make_category(self.store_a, "C", "c")
        p = make_product(self.store_a, cat, name="Alpha")
        r = self.client.get("/api/v1/products/", HTTP_HOST=HOST_STORE_A_CUSTOM)
        self.assertEqual(r.status_code, 200)
        ids = [row["public_id"] for row in r.data.get("results", r.data)]
        self.assertIn(p.public_id, ids)

    def test_platform_auth_token_and_health_no_tenant_required(self):
        user = make_user("platform-user@example.com", password="pw123456")
        make_membership(user, self.store_a)
        tok = self.client.post(
            "/api/v1/auth/token/",
            {"email": "platform-user@example.com", "password": "pw123456"},
            format="json",
            HTTP_HOST="localhost",
        )
        self.assertEqual(tok.status_code, 200)
        health = self.client.get("/health", HTTP_HOST="localhost")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json().get("status"), "ok")

    def test_platform_host_request_store_is_none(self):
        req = self.factory.get("/api/v1/auth/token/", HTTP_HOST="localhost")
        TenantResolutionMiddleware(lambda r: r).process_request(req)
        self.assertTrue(req.is_platform_request)
        self.assertIsNone(req.store)

        req_health = self.factory.get("/health", HTTP_HOST="localhost")
        TenantResolutionMiddleware(lambda r: r).process_request(req_health)
        self.assertTrue(req_health.is_platform_request)
        self.assertIsNone(req_health.store)

    def test_custom_domain_products_resolves_with_uppercase_host_header(self):
        cat = make_category(self.store_a, "C2", "c2")
        p = make_product(self.store_a, cat, name="CaseHost")
        r = self.client.get(
            "/api/v1/products/",
            HTTP_HOST=HOST_STORE_A_CUSTOM.upper(),
        )
        self.assertEqual(r.status_code, 200)
        ids = [row["public_id"] for row in r.data.get("results", r.data)]
        self.assertIn(p.public_id, ids)

    def test_products_list_on_localhost_without_tenant_context_forbidden(self):
        r = self.client.get("/api/v1/products/", HTTP_HOST="localhost")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json().get("detail"), "Unknown tenant host.")

    def test_rapid_unknown_host_product_requests_all_forbidden(self):
        for _ in range(10):
            r = self.client.get("/api/v1/products/", HTTP_HOST=HOST_UNKNOWN)
            self.assertEqual(r.status_code, 403, r.content)
            self.assertEqual(r.json().get("detail"), "Unknown tenant host.")


class CrossTenantDomainHostTests(TestCase):
    """Host header must scope storefront data; no auth headers (JWT would override host)."""

    def setUp(self):
        self.client = APIClient()
        self.store_a = make_store("Store A")
        self.store_b = make_store("Store B")
        pin_generated_domain(self.store_a, HOST_STORE_A)
        pin_generated_domain(self.store_b, HOST_STORE_B)
        self.cat_a = make_category(self.store_a, "Electronics", "electronics")
        self.cat_b = make_category(self.store_b, "Electronics", "electronics")
        self.product_a = make_product(self.store_a, self.cat_a, name="Product Alpha")
        self.product_b = make_product(self.store_b, self.cat_b, name="Product Beta")

    def test_product_list_never_includes_other_store(self):
        r = self.client.get("/api/v1/products/", HTTP_HOST=HOST_STORE_A)
        self.assertEqual(r.status_code, 200)
        ids = [row["public_id"] for row in r.data.get("results", r.data)]
        self.assertIn(self.product_a.public_id, ids)
        self.assertNotIn(self.product_b.public_id, ids)

    def test_product_detail_other_store_public_id_returns_404(self):
        r = self.client.get(
            f"/api/v1/products/{self.product_b.public_id}/",
            HTTP_HOST=HOST_STORE_A,
        )
        self.assertEqual(r.status_code, 404)

    def test_product_detail_other_store_slug_returns_404(self):
        r = self.client.get(
            f"/api/v1/products/{self.product_b.slug}/",
            HTTP_HOST=HOST_STORE_A,
        )
        self.assertEqual(r.status_code, 404)


class DomainModelConstraintTests(TestCase):
    def setUp(self):
        self.store_a = make_store("A")
        self.store_b = make_store("B")
        pin_generated_domain(self.store_a, HOST_STORE_A)
        pin_generated_domain(self.store_b, HOST_STORE_B)
        Domain.objects.create(
            store=self.store_a,
            domain=HOST_STORE_A_CUSTOM,
            is_custom=True,
            is_verified=True,
            is_primary=False,
            verification_token=None,
        )

    def test_duplicate_domain_across_stores_integrity_error(self):
        with self.assertRaises(IntegrityError):
            Domain.objects.create(
                store=self.store_b,
                domain=HOST_STORE_A_CUSTOM,
                is_custom=True,
                is_verified=True,
                is_primary=False,
                verification_token=None,
            )

    def test_duplicate_domain_serializer_validation(self):
        ser = CustomDomainCreateSerializer(data={"domain": HOST_STORE_A_CUSTOM})
        self.assertFalse(ser.is_valid())
        self.assertIn("domain", ser.errors)

    def test_second_generated_domain_per_store_integrity_error(self):
        with self.assertRaises(IntegrityError):
            Domain.objects.create(
                store=self.store_a,
                domain="second-generated.mybaas.com",
                is_custom=False,
                is_verified=True,
                is_primary=False,
                verification_token=None,
            )

    def test_second_custom_domain_per_store_integrity_error(self):
        with self.assertRaises(IntegrityError):
            Domain.objects.create(
                store=self.store_a,
                domain="other-custom.example.com",
                is_custom=True,
                is_verified=False,
                is_primary=False,
                verification_token="t",
            )

    def test_domain_save_lowercases_custom_hostname(self):
        store = make_store("NormSave")
        pin_generated_domain(store, "normsave.mybaas.com")
        dom = Domain(
            store=store,
            domain="MiXeD.Save.example.com",
            is_custom=True,
            is_verified=False,
            is_primary=False,
            verification_token="ns",
        )
        dom.save()
        dom.refresh_from_db()
        self.assertEqual(dom.domain, "mixed.save.example.com")


class DomainLifecycleApiTests(DomainDashboardApiTestMixin, TestCase):
    """Add → verify → use host; delete → old 403; new domain → new host works."""

    HOST_A = "lifecycle-a.example.com"
    HOST_B = "lifecycle-b.example.com"

    @patch("engine.apps.stores.domain_views.txt_record_contains_token", return_value=True)
    def test_replace_custom_domain_old_host_stops_working(self, _mock_txt):
        pub = self.store.public_id
        r1 = self.client.post(
            "/api/v1/stores/domains/",
            {"domain": self.HOST_A},
            format="json",
            HTTP_X_STORE_ID=pub,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r1.status_code, 201)
        pid_a = r1.data["public_id"]
        self.assertEqual(
            self.client.post(
                f"/api/v1/stores/domains/{pid_a}/verify/",
                format="json",
                HTTP_X_STORE_ID=pub,
                HTTP_HOST=self.platform_host,
            ).status_code,
            200,
        )
        cat = make_category(self.store)
        p = make_product(self.store, cat, name="LifeProd")
        anon = APIClient()
        ra = anon.get("/api/v1/products/", HTTP_HOST=self.HOST_A)
        self.assertEqual(ra.status_code, 200)
        ids_a = [row["public_id"] for row in ra.data.get("results", ra.data)]
        self.assertIn(p.public_id, ids_a)

        self.assertEqual(
            self.client.delete(
                f"/api/v1/stores/domains/{pid_a}/",
                HTTP_X_STORE_ID=pub,
                HTTP_HOST=self.platform_host,
            ).status_code,
            204,
        )
        self.assertEqual(anon.get("/api/v1/products/", HTTP_HOST=self.HOST_A).status_code, 403)

        r2 = self.client.post(
            "/api/v1/stores/domains/",
            {"domain": self.HOST_B},
            format="json",
            HTTP_X_STORE_ID=pub,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r2.status_code, 201)
        pid_b = r2.data["public_id"]
        self.assertEqual(
            self.client.post(
                f"/api/v1/stores/domains/{pid_b}/verify/",
                format="json",
                HTTP_X_STORE_ID=pub,
                HTTP_HOST=self.platform_host,
            ).status_code,
            200,
        )
        rb = anon.get("/api/v1/products/", HTTP_HOST=self.HOST_B)
        self.assertEqual(rb.status_code, 200)
        ids_b = [row["public_id"] for row in rb.data.get("results", rb.data)]
        self.assertIn(p.public_id, ids_b)
        self.assertEqual(anon.get("/api/v1/products/", HTTP_HOST=self.HOST_A).status_code, 403)


class ProvisionGeneratedDomainCollisionTests(TestCase):
    def test_provision_retries_when_generated_hostname_collides(self):
        blocked = "aaaaaaaaaa.mybaas.com"
        blocker = make_store("CollisionBlocker")
        pin_generated_domain(blocker, blocked)

        store = make_store("CollisionTarget")
        Domain.all_objects.filter(store=store).delete()

        real_randbelow = secrets.randbelow
        phase = {"n": 0}

        def randbelow_side(limit):
            phase["n"] += 1
            if phase["n"] == 1:
                return 2
            return real_randbelow(limit)

        with patch(
            "engine.apps.stores.services.secrets.randbelow",
            side_effect=randbelow_side,
        ), patch("engine.apps.stores.services.secrets.choice", return_value="a"):
            dom = provision_generated_domain(store)

        self.assertNotEqual(dom.domain.lower(), blocked)
        self.assertTrue(dom.domain.endswith(".mybaas.com"))
        self.assertEqual(Domain.objects.filter(store=store, is_custom=False).count(), 1)


class DomainWebSocketMiddlewareScopeTests(TransactionTestCase):
    def setUp(self):
        self.store_a = make_store("WsScopeStore")
        pin_generated_domain(self.store_a, HOST_STORE_A)

    async def test_scope_store_public_id_is_str_not_store_model(self):
        captured = {}

        async def inner(scope, receive, send):
            captured["scope"] = scope

        mw = DomainWebSocketMiddleware(inner)
        scope = {"type": "websocket", "headers": [(b"host", HOST_STORE_A.encode())]}
        await mw(scope, None, None)
        s = captured["scope"]
        pid = s.get("store_public_id")
        self.assertIsInstance(pid, str)
        self.assertEqual(pid, self.store_a.public_id)
        self.assertNotIn("store", s)
        for v in s.values():
            self.assertFalse(isinstance(v, Store))


class MiddlewareSafetyTests(TestCase):
    """request.store and is_platform_request invariants."""

    def setUp(self):
        _clear_tenant_resolution_cache()
        self.factory = RequestFactory()
        self.store_a = make_store("Store A")
        pin_generated_domain(self.store_a, HOST_STORE_A)

    def test_middleware_matrix(self):
        cases = [
            # host, path, expect_platform, expect_store_pk
            ("localhost", "/api/v1/auth/token/", True, None),
            ("localhost", "/health", True, None),
            (HOST_STORE_A, "/api/v1/products/", False, self.store_a.pk),
            (HOST_UNKNOWN, "/api/v1/products/", False, None),
            (HOST_STORE_A, "/health", False, self.store_a.pk),
        ]
        for host, path, expect_platform, expect_store_pk in cases:
            with self.subTest(host=host, path=path):
                req = self.factory.get(path, HTTP_HOST=host)
                TenantResolutionMiddleware(lambda r: r).process_request(req)
                self.assertEqual(
                    getattr(req, "is_platform_request", None),
                    expect_platform,
                    msg="is_platform_request",
                )
                if expect_store_pk is None:
                    self.assertIsNone(req.store, msg="store")
                else:
                    self.assertIsNotNone(req.store)
                    self.assertEqual(req.store.pk, expect_store_pk)

    def test_guard_blocks_tenant_api_without_store(self):
        req = self.factory.get("/api/v1/products/", HTTP_HOST=HOST_UNKNOWN)
        guard_resp = _apply_tenant_middleware(req)
        self.assertIsNotNone(guard_resp)
        self.assertEqual(guard_resp.status_code, 403)

    def test_guard_allows_exempt_auth_on_non_platform_unknown_host(self):
        """Auth prefix is exempt from tenant guard even if host does not resolve."""
        req = self.factory.get("/api/v1/auth/token/", HTTP_HOST=HOST_UNKNOWN)
        guard_resp = _apply_tenant_middleware(req)
        self.assertIsNone(guard_resp)


class DomainWebSocketIsolationTests(TransactionTestCase):
    """Channels: domain middleware + JWT + group isolation."""

    def setUp(self):
        self.store_a = make_store("Store A")
        self.store_b = make_store("Store B")
        pin_generated_domain(self.store_a, HOST_STORE_A)
        pin_generated_domain(self.store_b, HOST_STORE_B)
        self.user_a = make_user("ws-a@example.com", password="pw123456")
        self.user_b = make_user("ws-b@example.com", password="pw123456")
        make_membership(self.user_a, self.store_a)
        make_membership(self.user_b, self.store_b)
        self.token_a = obtain_access_token("ws-a@example.com", "pw123456")
        self.token_b = obtain_access_token("ws-b@example.com", "pw123456")

    def _ws_headers(self, host: str):
        origin = f"http://{host}"
        return [
            (b"host", host.encode()),
            (b"origin", origin.encode()),
        ]

    async def test_connect_accepts_and_receives_store_event(self):
        path = f"/ws/v1/store/events/?token={self.token_a}"
        communicator = WebsocketCommunicator(
            application,
            path,
            headers=self._ws_headers(HOST_STORE_A),
        )
        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await sync_to_async(emit_store_event)(
            self.store_a.public_id,
            "order.created",
            {"order_public_id": "ord_test"},
        )
        raw = await communicator.receive_from()
        data = json.loads(raw)
        self.assertEqual(data.get("event"), "order.created")
        self.assertEqual(data.get("payload", {}).get("order_public_id"), "ord_test")
        await communicator.disconnect()

    async def test_invalid_host_connection_rejected(self):
        path = f"/ws/v1/store/events/?token={self.token_a}"
        communicator = WebsocketCommunicator(
            application,
            path,
            headers=self._ws_headers(HOST_UNKNOWN),
        )
        connected, _ = await communicator.connect()
        self.assertFalse(connected)
        await communicator.disconnect()

    async def test_cross_tenant_group_send_not_received(self):
        path = f"/ws/v1/store/events/?token={self.token_a}"
        comm_a = WebsocketCommunicator(
            application,
            path,
            headers=self._ws_headers(HOST_STORE_A),
        )
        self.assertTrue((await comm_a.connect())[0])
        await sync_to_async(emit_store_event)(
            self.store_b.public_id,
            "order.created",
            {"leak": True},
        )
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(comm_a.receive_from(), timeout=0.2)
        await comm_a.disconnect()

    async def test_peer_store_receives_its_own_broadcast(self):
        path_b = f"/ws/v1/store/events/?token={self.token_b}"
        comm_b = WebsocketCommunicator(
            application,
            path_b,
            headers=self._ws_headers(HOST_STORE_B),
        )
        self.assertTrue((await comm_b.connect())[0])
        await sync_to_async(emit_store_event)(
            self.store_b.public_id,
            "order.created",
            {"for_b": 1},
        )
        raw = await comm_b.receive_from()
        data = json.loads(raw)
        self.assertEqual(data.get("event"), "order.created")
        self.assertEqual(data.get("payload", {}).get("for_b"), 1)
        await comm_b.disconnect()

    async def test_order_created_signal_emits_to_connected_client(self):
        path = f"/ws/v1/store/events/?token={self.token_a}"
        communicator = WebsocketCommunicator(
            application,
            path,
            headers=self._ws_headers(HOST_STORE_A),
        )
        self.assertTrue((await communicator.connect())[0])
        zone = await sync_to_async(ShippingZone.objects.create)(
            store=self.store_a,
            name="Signal Zone",
            is_active=True,
        )
        await sync_to_async(
            lambda: Order.objects.create(store=self.store_a, shipping_zone=zone)
        )()
        raw = await communicator.receive_from()
        data = json.loads(raw)
        self.assertIn(data.get("event"), ("order.created", "order.updated"))
        self.assertIn("order_public_id", data.get("payload", {}))
        await communicator.disconnect()

    async def test_rejected_connection_does_not_receive_foreign_events(self):
        path = f"/ws/v1/store/events/?token={self.token_a}"
        communicator = WebsocketCommunicator(
            application,
            path,
            headers=self._ws_headers(HOST_UNKNOWN),
        )
        self.assertFalse((await communicator.connect())[0])
        await sync_to_async(emit_store_event)(
            self.store_a.public_id,
            "order.created",
            {"after_close": True},
        )
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(communicator.receive_from(), timeout=0.15)
        await communicator.disconnect()

    async def test_ws_token_store_must_match_connection_host(self):
        """JWT scoped to store A must not connect when Host resolves to store B."""
        path = f"/ws/v1/store/events/?token={self.token_a}"
        communicator = WebsocketCommunicator(
            application,
            path,
            headers=self._ws_headers(HOST_STORE_B),
        )
        self.assertFalse((await communicator.connect())[0])
        await communicator.disconnect()


@override_settings(
    TENANT_RESOLUTION_RATE_LIMIT_IP=500,
    TENANT_RESOLUTION_RATE_LIMIT_DOMAIN=1,
)
class WebSocketRateLimitTests(TransactionTestCase):
    def setUp(self):
        _clear_tenant_resolution_cache()
        self.store_a = make_store("WS RL A")
        pin_generated_domain(self.store_a, HOST_WS_RATE_LIMIT, verified=True)
        self.user_a = make_user("ws-rl-a@example.com", password="pw123456")
        make_membership(self.user_a, self.store_a)
        self.token_a = obtain_access_token("ws-rl-a@example.com", "pw123456")

    def _ws_headers(self, host: str):
        origin = f"http://{host}"
        return [
            (b"host", host.encode()),
            (b"origin", origin.encode()),
        ]

    async def test_second_websocket_handshake_on_same_host_is_rate_limited(self):
        path_a = f"/ws/v1/store/events/?token={self.token_a}"
        comm1 = WebsocketCommunicator(
            application,
            path_a,
            headers=self._ws_headers(HOST_WS_RATE_LIMIT),
        )
        self.assertTrue((await comm1.connect())[0])
        comm2 = WebsocketCommunicator(
            application,
            path_a,
            headers=self._ws_headers(HOST_WS_RATE_LIMIT),
        )
        self.assertFalse((await comm2.connect())[0])
        await comm1.disconnect()
        await comm2.disconnect()


class DomainResolutionCacheTests(TestCase):
    """Cache hit/miss and invalidation for verified domain resolution."""

    def setUp(self):
        _clear_tenant_resolution_cache()
        self.store = make_store("CacheStore")
        pin_generated_domain(self.store, "cache-hit.mybaas.com", verified=True)

    def test_second_payload_fetch_is_cached(self):
        host = "cache-hit.mybaas.com"
        with CaptureQueriesContext(connection) as ctx:
            get_domain_resolution_payload(host)
        self.assertGreater(len(ctx), 0)
        with CaptureQueriesContext(connection) as ctx:
            get_domain_resolution_payload(host)
        self.assertEqual(len(ctx), 0)

    def test_cache_invalidates_when_marked_unverified(self):
        host = "cache-hit.mybaas.com"
        self.assertIsNotNone(get_domain_resolution_payload(host))
        d = Domain.objects.get(domain=host)
        d.is_verified = False
        d.save(update_fields=["is_verified", "updated_at"])
        self.assertIsNone(get_domain_resolution_payload(host))


@override_settings(
    TENANT_RESOLUTION_RATE_LIMIT_IP=100,
    TENANT_RESOLUTION_RATE_LIMIT_DOMAIN=2,
)
class TenantResolutionRateLimitTests(TestCase):
    def setUp(self):
        _clear_tenant_resolution_cache()
        self.client = APIClient()
        self.store = make_store("RLStore")
        pin_generated_domain(self.store, HOST_HTTP_RATE_LIMIT, verified=True)

    def test_unknown_host_429_same_message(self):
        for _ in range(2):
            r = self.client.get("/api/v1/products/", HTTP_HOST=HOST_UNKNOWN)
            self.assertEqual(r.status_code, 403)
        r3 = self.client.get("/api/v1/products/", HTTP_HOST=HOST_UNKNOWN)
        self.assertEqual(r3.status_code, 429)
        self.assertEqual(r3.json().get("detail"), "Too many requests.")

    def test_known_host_429_uses_identical_body(self):
        for _ in range(2):
            r = self.client.get("/api/v1/products/", HTTP_HOST=HOST_HTTP_RATE_LIMIT)
            self.assertEqual(r.status_code, 200)
        r3 = self.client.get("/api/v1/products/", HTTP_HOST=HOST_HTTP_RATE_LIMIT)
        self.assertEqual(r3.status_code, 429)
        self.assertEqual(r3.json().get("detail"), "Too many requests.")


class SubdomainAbuseRegressionTests(TestCase):
    """Only exact Domain rows match; no implicit subdomains."""

    def setUp(self):
        self.client = APIClient()
        self.store = make_store("SubAbuse")
        self.parent_host = "exact-parent.example.com"
        pin_generated_domain(self.store, "sub-abuse-gen.mybaas.com", verified=True)
        Domain.objects.create(
            store=self.store,
            domain=self.parent_host,
            is_custom=True,
            is_verified=True,
            is_primary=False,
            verification_token=None,
        )

    def test_child_subdomain_does_not_resolve_without_row(self):
        sub = "child." + self.parent_host
        self.assertFalse(Domain.objects.filter(domain=sub).exists())
        r = self.client.get("/api/v1/products/", HTTP_HOST=sub)
        self.assertEqual(r.status_code, 403)


class DomainSoftDeleteRestoreTests(DomainDashboardApiTestMixin, TestCase):
    @patch("engine.apps.stores.domain_views.txt_record_contains_token", return_value=True)
    def test_soft_deleted_custom_host_stops_resolving_then_restore(self, _mock_txt):
        anon = APIClient()
        host = "soft-lg.example.com"
        self.client.post(
            "/api/v1/stores/domains/",
            {"domain": host},
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        pid = Domain.objects.get(domain=host).public_id
        self.client.post(
            f"/api/v1/stores/domains/{pid}/verify/",
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(anon.get("/api/v1/products/", HTTP_HOST=host).status_code, 200)
        self.client.delete(
            f"/api/v1/stores/domains/{pid}/",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(anon.get("/api/v1/products/", HTTP_HOST=host).status_code, 403)
        r_restore = self.client.post(
            f"/api/v1/stores/domains/{pid}/restore/",
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
            HTTP_HOST=self.platform_host,
        )
        self.assertEqual(r_restore.status_code, 200)
        self.assertEqual(anon.get("/api/v1/products/", HTTP_HOST=host).status_code, 200)
