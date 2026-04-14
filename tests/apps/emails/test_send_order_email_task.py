"""Tests for ORDER_CONFIRMED task (structured order context)."""

import uuid as _uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from engine.apps.emails.constants import ORDER_CONFIRMED
from engine.apps.emails.exceptions import SecurityError
from engine.apps.emails.tasks import send_order_email_task
from engine.apps.orders.models import Order
from engine.apps.shipping.models import ShippingZone
from tests.apps.emails.test_triggers import _store_with_owner_and_settings


def _order(store, **kwargs):
    order_number = f"T{_uuid.uuid4().hex[:12].upper()}"
    zone = ShippingZone.objects.create(
        store=store,
        name=f"Zone {_uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    defaults = dict(
        store=store,
        order_number=order_number,
        email="cust@example.com",
        shipping_name="Jane",
        shipping_zone=zone,
        total=Decimal("100.00"),
        shipping_cost=Decimal("10.00"),
        district="Dhaka",
        shipping_address="1 Test Road",
        phone="01700000000",
    )
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


class SendOrderEmailTaskTests(TestCase):
    @patch("engine.apps.emails.triggers.has_feature", return_value=True)
    @patch("engine.apps.emails.tasks.send_email")
    def test_send_email_context_includes_order_summary(self, mock_send, _hf):
        store = _store_with_owner_and_settings()
        order = _order(store)
        send_order_email_task.run(str(order.public_id), order.store.public_id)
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args[0][0], ORDER_CONFIRMED)
        ctx = mock_send.call_args[0][2]
        self.assertEqual(ctx["store_public_id"], store.public_id)
        self.assertIn("order_summary", ctx)
        self.assertIn("District: Dhaka", ctx["order_summary"])
        self.assertIn("Delivery charge:", ctx["order_summary"])
        self.assertIn("Total: 100.00", ctx["order_summary"])
        order.refresh_from_db()
        self.assertIsNotNone(order.customer_confirmation_sent_at)

    @patch("engine.apps.emails.triggers.has_feature", return_value=True)
    @patch("engine.apps.emails.tasks.send_email")
    def test_tenant_mismatch_raises_and_does_not_send(self, mock_send, _hf):
        store_a = _store_with_owner_and_settings()
        store_b = _store_with_owner_and_settings()
        order = _order(store_a)
        with self.assertRaises(SecurityError):
            send_order_email_task.run(str(order.public_id), store_b.public_id)
        mock_send.assert_not_called()
        order.refresh_from_db()
        self.assertIsNone(order.customer_confirmation_sent_at)
