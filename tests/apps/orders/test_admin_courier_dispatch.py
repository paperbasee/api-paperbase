"""Admin courier dispatch, bulk dispatch, and customer email queueing."""

from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from engine.apps.couriers.models import Courier
from engine.apps.orders.models import Order
from engine.apps.stores.models import StoreMembership
from engine.core.encryption import encrypt_value

from tests.core.test_core import (
    _default_shipping_zone,
    _ensure_default_plan,
    _make_membership,
    _make_order,
    _make_store,
    make_user,
)


class AdminCourierDispatchTests(TestCase):
    def setUp(self):
        _ensure_default_plan()
        self.client = APIClient()
        self.store = _make_store("Courier Test Store", "courier-test.local")
        self.user = make_user("courier-admin@example.com")
        _make_membership(self.user, self.store, StoreMembership.Role.OWNER)
        self.zone = _default_shipping_zone(self.store)
        self.order = _make_order(
            self.store,
            "buyer@example.com",
            status=Order.Status.CONFIRMED,
            shipping_zone=self.zone,
            district="Dhaka",
            shipping_address="Test Address, Dhanmondi",
        )
        Courier.objects.create(
            store=self.store,
            provider=Courier.Provider.STEADFAST,
            api_key_encrypted=encrypt_value("api"),
            secret_key_encrypted=encrypt_value("secret"),
            is_active=True,
        )

    def _auth(self):
        self.client.force_authenticate(user=self.user)
        self.client.credentials(HTTP_X_STORE_PUBLIC_ID=self.store.public_id)

    @patch("engine.apps.orders.admin_views.queue_customer_order_dispatched_email")
    @patch("engine.apps.orders.admin_views.run_courier_api", return_value={"consignment_id": "C-99"})
    def test_send_to_courier_requires_confirmed(self, _mock_api, mock_queue):
        self._auth()
        self.order.status = Order.Status.PENDING
        self.order.save(update_fields=["status"])
        resp = self.client.post(
            f"/api/v1/admin/orders/{self.order.public_id}/send-to-courier/",
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        mock_queue.assert_not_called()

    @patch("engine.apps.orders.admin_views.queue_customer_order_dispatched_email")
    @patch("engine.apps.orders.admin_views.run_courier_api", return_value={"consignment_id": "C-99"})
    def test_send_to_courier_calls_queue_after_courier(self, _mock_api, mock_queue):
        self._auth()
        resp = self.client.post(
            f"/api/v1/admin/orders/{self.order.public_id}/send-to-courier/",
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.get("sent_to_courier"))
        _mock_api.assert_called_once()
        mock_queue.assert_called_once()

    @patch("engine.apps.orders.admin_views.queue_customer_order_dispatched_email")
    def test_status_patch_to_confirmed_does_not_queue_customer_email(self, mock_queue):
        self._auth()
        self.order.status = Order.Status.PENDING
        self.order.save(update_fields=["status"])
        resp = self.client.patch(
            f"/api/v1/admin/orders/{self.order.public_id}/status/",
            {"status": "confirmed"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        mock_queue.assert_not_called()

    @patch("engine.apps.orders.admin_views.queue_customer_order_dispatched_email")
    @patch("engine.apps.orders.admin_views.run_courier_api", return_value={"consignment_id": "B-1"})
    def test_bulk_confirm_send_courier_returns_summary(self, _mock_api, mock_queue):
        self._auth()
        resp = self.client.post(
            "/api/v1/admin/orders/bulk-confirm-send-courier/",
            {"order_public_ids": [self.order.public_id]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["summary"]["ok"], 1)
        self.assertEqual(resp.data["summary"]["failed"], 0)
        self.assertTrue(resp.data["results"][0]["ok"])
        mock_queue.assert_called_once()
