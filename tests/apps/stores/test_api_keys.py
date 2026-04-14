"""API key tenancy enforcement tests."""

import json

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from config.asgi import application
from engine.core.store_api_key_auth import (
    TENANT_API_KEY_REQUIRED_DETAIL,
    requires_tenant_api_key,
)
from engine.core.tenant_execution import tenant_scope_from_store
from engine.apps.inventory.models import Inventory
from engine.apps.notifications.models import PlatformNotification
from engine.apps.products.models import Category, Product
from engine.apps.stores.models import Store, StoreMembership
from engine.apps.stores.services import (
    allocate_unique_store_code,
    create_store_api_key,
    normalize_store_code_base_from_name,
    revoke_store_api_key,
)
from django.contrib.auth import get_user_model
from tests.core.test_core import _ensure_default_plan, _ensure_subscription

User = get_user_model()


def _response_payload(response):
    """DRF responses expose .data; tenant API key middleware returns JsonResponse."""
    if hasattr(response, "data"):
        return response.data
    return json.loads(response.content)


def make_store(name: str) -> Store:
    base = normalize_store_code_base_from_name(name) or "T"
    email = f"{name.lower().replace(' ', '')}@example.com"
    owner = User.objects.create_user(email=email, password="pass1234", is_verified=True)
    store = Store.objects.create(
        owner=owner,
        name=name,
        code=allocate_unique_store_code(base),
        owner_name=f"{name} Owner",
        owner_email=email,
    )
    StoreMembership.objects.create(
        user=owner,
        store=store,
        role=StoreMembership.Role.OWNER,
        is_active=True,
    )
    return store


def make_product(store: Store, *, name: str) -> Product:
    with tenant_scope_from_store(store=store, reason="test fixture"):
        category = Category.objects.create(
            store=store,
            name=f"{name} Cat",
            slug="",
        )
        p = Product.objects.create(
            store=store,
            category=category,
            name=name,
            price=10,
            stock=5,
            status=Product.Status.ACTIVE,
            is_active=True,
        )
        Inventory.objects.get_or_create(
            product=p,
            variant=None,
            defaults={"quantity": 5},
        )
    return p


@override_settings(TENANT_API_KEY_ENFORCE=True)
class APIKeyTenantEnforcementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store_a = make_store("Store A")
        self.store_b = make_store("Store B")
        self.key_row_a, self.key_a = create_store_api_key(self.store_a, name="Frontend A")
        _key_row_b, self.key_b = create_store_api_key(self.store_b, name="Frontend B")
        self.product_a = make_product(self.store_a, name="Alpha")
        self.product_b = make_product(self.store_b, name="Beta")

    def test_missing_key_returns_401(self):
        response = self.client.get("/api/v1/products/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            _response_payload(response).get("detail"),
            TENANT_API_KEY_REQUIRED_DETAIL,
        )

    def test_invalid_key_returns_401(self):
        response = self.client.get(
            "/api/v1/products/",
            HTTP_AUTHORIZATION="Bearer ak_live_invalid",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            _response_payload(response).get("detail"),
            TENANT_API_KEY_REQUIRED_DETAIL,
        )

    def test_revoked_key_returns_401(self):
        revoke_store_api_key(self.key_row_a)
        response = self.client.get(
            "/api/v1/products/",
            HTTP_AUTHORIZATION=f"Bearer {self.key_a}",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            _response_payload(response).get("detail"),
            TENANT_API_KEY_REQUIRED_DETAIL,
        )

    def test_valid_key_returns_only_store_data(self):
        response = self.client.get(
            "/api/v1/products/",
            HTTP_AUTHORIZATION=f"Bearer {self.key_a}",
        )
        self.assertEqual(response.status_code, 200)
        ids = [row["public_id"] for row in response.data.get("results", response.data)]
        self.assertIn(self.product_a.public_id, ids)
        self.assertNotIn(self.product_b.public_id, ids)

    def test_multiple_active_keys_for_same_store_are_valid(self):
        row2, key2 = create_store_api_key(self.store_a, name="Secondary")
        self.assertNotEqual(row2.public_id, self.key_row_a.public_id)
        response_primary = self.client.get(
            "/api/v1/products/",
            HTTP_AUTHORIZATION=f"Bearer {self.key_a}",
        )
        response_secondary = self.client.get(
            "/api/v1/products/",
            HTTP_AUTHORIZATION=f"Bearer {key2}",
        )
        self.assertEqual(response_primary.status_code, 200)
        self.assertEqual(response_secondary.status_code, 200)

    def test_api_key_last_used_at_updates_after_request(self):
        self.key_row_a.refresh_from_db()
        self.assertIsNone(self.key_row_a.last_used_at)
        response = self.client.get(
            "/api/v1/products/",
            HTTP_AUTHORIZATION=f"Bearer {self.key_a}",
        )
        self.assertEqual(response.status_code, 200)
        self.key_row_a.refresh_from_db()
        self.assertIsNotNone(self.key_row_a.last_used_at)

    def test_route_matrix_guard_for_api_key_enforcement(self):
        self.assertFalse(requires_tenant_api_key("/api/v1/auth/token/"))
        self.assertFalse(requires_tenant_api_key("/api/v1/store/"))
        self.assertFalse(requires_tenant_api_key("/api/v1/admin/products/"))
        self.assertFalse(requires_tenant_api_key("/api/v1/system-notifications/active/"))
        self.assertFalse(requires_tenant_api_key("/api/v1/settings/network/api-keys/"))
        self.assertFalse(requires_tenant_api_key("/api/v1/billing/payment/pending/"))
        self.assertTrue(requires_tenant_api_key("/api/v1/products/"))
        self.assertTrue(requires_tenant_api_key("/api/v1/categories/"))
        self.assertTrue(requires_tenant_api_key("/api/v1/support/"))
        self.assertFalse(requires_tenant_api_key("/health"))


class APIKeyWebSocketTests(TransactionTestCase):
    def setUp(self):
        self.store = make_store("Websocket Store")
        _ensure_subscription(self.store.owner)
        _row, self.api_key = create_store_api_key(self.store, name="Realtime")

    def test_ws_requires_api_key(self):
        async def _run():
            communicator = WebsocketCommunicator(application, "/ws/v1/store/events/")
            connected, _ = await communicator.connect()
            self.assertFalse(connected)
            await communicator.disconnect()

        async_to_sync(_run)()

    def test_ws_accepts_valid_api_key(self):
        async def _run():
            path = f"/ws/v1/store/events/?api_key={self.api_key}"
            communicator = WebsocketCommunicator(application, path)
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.disconnect()

        async_to_sync(_run)()

    def test_ws_accepts_valid_bearer_header_api_key(self):
        async def _run():
            communicator = WebsocketCommunicator(
                application,
                "/ws/v1/store/events/",
                headers=[(b"authorization", f"Bearer {self.api_key}".encode("utf-8"))],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.disconnect()

        async_to_sync(_run)()

    def test_ws_rejects_revoked_api_key(self):
        row, key = create_store_api_key(self.store, name="Revoked")
        revoke_store_api_key(row)

        async def _run():
            communicator = WebsocketCommunicator(application, f"/ws/v1/store/events/?api_key={key}")
            connected, _ = await communicator.connect()
            self.assertFalse(connected)
            await communicator.disconnect()

        async_to_sync(_run)()


@override_settings(TENANT_API_KEY_ENFORCE=True)
class JWTExemptRoutesTests(TestCase):
    def setUp(self):
        _ensure_default_plan()
        self.client = APIClient()
        self.store = make_store("Dashboard Store")
        self.user = User.objects.create_user(
            email="dashboard@example.com",
            password="secret123",
            is_verified=True,
        )
        StoreMembership.objects.create(
            user=self.user,
            store=self.store,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        )
        token_response = self.client.post(
            "/api/v1/auth/token/",
            {"email": "dashboard@example.com", "password": "secret123"},
            format="json",
        )
        self.assertEqual(token_response.status_code, 200)
        self.access = token_response.data["access"]
        self.refresh = token_response.data["refresh"]

    def test_auth_token_refresh_route_is_not_blocked_by_api_key_middleware(self):
        response = self.client.post(
            "/api/v1/auth/token/refresh/",
            {"refresh": self.refresh},
            format="json",
        )
        self.assertNotEqual(response.data.get("detail"), TENANT_API_KEY_REQUIRED_DETAIL)
        self.assertIn(response.status_code, {200, 401})

    def test_auth_me_works_with_jwt_without_api_key(self):
        response = self.client.get(
            "/api/v1/auth/me/",
            HTTP_AUTHORIZATION=f"Bearer {self.access}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["public_id"], self.user.public_id)

    def test_stores_route_is_jwt_only_not_api_key_required(self):
        response = self.client.get(
            "/api/v1/store/",
            HTTP_AUTHORIZATION=f"Bearer {self.access}",
        )
        self.assertEqual(response.status_code, 200)

    def test_system_notifications_route_is_jwt_only_not_api_key_required(self):
        PlatformNotification.objects.create(
            title="System update",
            message="Maintenance",
            is_active=True,
            start_at=timezone.now(),
        )
        response = self.client.get(
            "/api/v1/system-notifications/active/",
            HTTP_AUTHORIZATION=f"Bearer {self.access}",
        )
        self.assertEqual(response.status_code, 200)

    def test_regenerate_endpoint_invalidates_old_key_immediately(self):
        key_row, old_key = create_store_api_key(self.store, name="Initial")
        make_product(self.store, name="Regen Product")

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        regen_response = self.client.post(
            f"/api/v1/settings/network/api-keys/{key_row.public_id}/regenerate/",
            {"name": "Rotated"},
            format="json",
        )
        self.assertEqual(regen_response.status_code, 201)
        new_key = regen_response.data["api_key"]
        self.assertTrue(new_key.startswith("ak_pk_"))

        self.client.force_authenticate(user=None)
        old_key_response = self.client.get(
            "/api/v1/products/",
            HTTP_AUTHORIZATION=f"Bearer {old_key}",
        )
        self.assertEqual(old_key_response.status_code, 401)

        new_key_response = self.client.get(
            "/api/v1/products/",
            HTTP_AUTHORIZATION=f"Bearer {new_key}",
        )
        self.assertEqual(new_key_response.status_code, 200)

    def test_create_endpoint_revokes_previous_key(self):
        _key_row, old_key = create_store_api_key(self.store, name="Initial")
        make_product(self.store, name="Create Rotate Product")

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        create_response = self.client.post(
            "/api/v1/settings/network/api-keys/",
            {"name": "Rotated via create"},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        new_key = create_response.data["api_key"]
        self.assertTrue(new_key.startswith("ak_pk_"))

        self.client.force_authenticate(user=None)
        old_key_response = self.client.get(
            "/api/v1/products/",
            HTTP_AUTHORIZATION=f"Bearer {old_key}",
        )
        self.assertEqual(old_key_response.status_code, 401)

        new_key_response = self.client.get(
            "/api/v1/products/",
            HTTP_AUTHORIZATION=f"Bearer {new_key}",
        )
        self.assertEqual(new_key_response.status_code, 200)
