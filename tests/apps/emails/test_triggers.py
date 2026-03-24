"""Tests for queued transactional emails (Celery task stubs)."""

import uuid as _uuid
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from engine.apps.billing.models import Plan, Subscription
from engine.apps.billing.services import activate_subscription
from engine.apps.stores.models import Domain, Store, StoreMembership, StoreSettings
from engine.apps.emails.constants import (
    ORDER_CONFIRMED,
    ORDER_RECEIVED,
    PLATFORM_NEW_SUBSCRIPTION,
    SUBSCRIPTION_ACTIVATED,
    SUBSCRIPTION_CHANGED,
    SUBSCRIPTION_PAYMENT,
    TWO_FA_DISABLE,
)
from engine.apps.emails.triggers import (
    notify_customer_order_confirmation_send_to_courier,
    notify_store_new_order,
    queue_two_fa_disabled_email,
)
from engine.apps.orders.models import Order
from engine.apps.shipping.models import ShippingZone

from tests.core.test_core import _ensure_default_plan

User = get_user_model()


def _store():
    d = f"t{_uuid.uuid4().hex[:12]}.local"
    store = Store.objects.create(
        name="S",
        domain=None,
        owner_name="O",
        owner_email=f"owner@{d}",
    )
    Domain.objects.filter(store=store, is_custom=False).update(domain=d)
    return store


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
    )
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


def _store_with_owner_and_settings(
    *,
    email_notify_owner: bool = True,
    email_notify_customer: bool = True,
):
    store = _store()
    User.objects.create_user(email=store.owner_email, password="pass")
    owner = User.objects.get(email=store.owner_email)
    StoreMembership.objects.create(
        user=owner,
        store=store,
        role=StoreMembership.Role.OWNER,
        is_active=True,
    )
    settings, _ = StoreSettings.objects.get_or_create(store=store)
    settings.email_notify_owner_on_order_received = email_notify_owner
    settings.email_customer_on_order_confirmed = email_notify_customer
    settings.save()
    return store


class NotifyStoreNewOrderTests(TestCase):
    @patch("engine.apps.emails.triggers.has_feature", return_value=True)
    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_sends_store_internal_only(self, mock_delay, _mock_hf):
        store = _store_with_owner_and_settings()
        order = _order(store)
        notify_store_new_order(order)
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args[0][0], ORDER_RECEIVED)
        self.assertEqual(mock_delay.call_args[0][1], store.owner_email)

    @patch("engine.apps.emails.triggers.has_feature", return_value=True)
    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_uses_contact_email_when_set(self, mock_delay, _mock_hf):
        store = _store_with_owner_and_settings()
        store.contact_email = "store@example.com"
        store.save()
        order = _order(store)
        notify_store_new_order(order)
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args[0][1], "store@example.com")

    @patch("engine.apps.emails.triggers.has_feature", return_value=True)
    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_skips_when_no_internal_email(self, mock_delay, _mock_hf):
        store = _store_with_owner_and_settings()
        store.contact_email = ""
        store.owner_email = ""
        store.save()
        order = _order(store)
        notify_store_new_order(order)
        mock_delay.assert_not_called()

    @patch("engine.apps.emails.triggers.has_feature", return_value=True)
    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_skips_when_setting_off(self, mock_delay, _mock_hf):
        store = _store_with_owner_and_settings(email_notify_owner=False)
        order = _order(store)
        notify_store_new_order(order)
        mock_delay.assert_not_called()


class NotifyStoreNewOrderNonPremiumTests(TestCase):
    @patch("engine.apps.emails.triggers.has_feature", return_value=False)
    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_skips_without_premium_feature(self, mock_delay, _mock_hf):
        store = _store_with_owner_and_settings()
        order = _order(store)
        notify_store_new_order(order)
        mock_delay.assert_not_called()


class CustomerConfirmationSendToCourierTests(TestCase):
    @patch("engine.apps.emails.triggers.has_feature", return_value=True)
    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_queues_confirmation_once(self, mock_delay, _mock_hf):
        store = _store_with_owner_and_settings()
        order = _order(store)
        self.assertTrue(notify_customer_order_confirmation_send_to_courier(order))
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args[0][0], ORDER_CONFIRMED)
        order.customer_confirmation_sent_at = timezone.now()
        order.save()
        mock_delay.reset_mock()
        self.assertFalse(notify_customer_order_confirmation_send_to_courier(order))
        mock_delay.assert_not_called()

    @patch("engine.apps.emails.triggers.has_feature", return_value=True)
    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_skips_when_setting_off(self, mock_delay, _mock_hf):
        store = _store_with_owner_and_settings(email_notify_customer=False)
        order = _order(store)
        self.assertFalse(notify_customer_order_confirmation_send_to_courier(order))
        mock_delay.assert_not_called()


class SubscriptionPaymentEmailTests(TestCase):
    def setUp(self):
        _ensure_default_plan()
        self.user = User.objects.create_user(email="sub@example.com", password="pass")
        self.plan = Plan.objects.filter(name="premium").first()
        self.assertIsNotNone(self.plan, "premium plan must exist from migrations")

    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_manual_zero_sends_activation_not_payment(self, mock_delay):
        activate_subscription(
            self.user,
            self.plan,
            source="manual",
            amount=0,
            provider="manual",
        )
        types_queued = [c.args[0] for c in mock_delay.call_args_list]
        self.assertIn(SUBSCRIPTION_ACTIVATED, types_queued)
        self.assertNotIn(SUBSCRIPTION_PAYMENT, types_queued)

    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_payment_source_sends_receipt_and_activation(self, mock_delay):
        activate_subscription(
            self.user,
            self.plan,
            source=Subscription.Source.PAYMENT,
            amount=0,
            provider="manual",
        )
        types_queued = [c.args[0] for c in mock_delay.call_args_list]
        self.assertIn(SUBSCRIPTION_PAYMENT, types_queued)
        self.assertIn(SUBSCRIPTION_ACTIVATED, types_queued)

    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_plan_change_sends_changed_not_activation(self, mock_delay):
        basic = Plan.objects.filter(is_default=True).first()
        self.assertIsNotNone(basic)
        activate_subscription(self.user, basic, source="manual", amount=0, provider="manual")
        mock_delay.reset_mock()
        activate_subscription(
            self.user,
            self.plan,
            source="manual",
            amount=0,
            provider="manual",
            change_reason="test",
        )
        types_queued = [c.args[0] for c in mock_delay.call_args_list]
        self.assertIn(SUBSCRIPTION_CHANGED, types_queued)
        self.assertNotIn(SUBSCRIPTION_ACTIVATED, types_queued)
        self.assertNotIn(PLATFORM_NEW_SUBSCRIPTION, types_queued)


class PlatformNewSubscriptionEmailTests(TestCase):
    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_platform_email_when_superuser_exists(self, mock_delay):
        User.objects.create_superuser(email="admin@example.com", password="adminpass")
        user = User.objects.create_user(email="u@example.com", password="pass")
        plan = Plan.objects.filter(name="premium").first()
        activate_subscription(user, plan, source="manual", amount=0, provider="manual")
        types_queued = [c.args[0] for c in mock_delay.call_args_list]
        self.assertIn(PLATFORM_NEW_SUBSCRIPTION, types_queued)
        admin_calls = [c for c in mock_delay.call_args_list if c.args[0] == PLATFORM_NEW_SUBSCRIPTION]
        self.assertTrue(any(c.args[1] == "admin@example.com" for c in admin_calls))


class TwoFactorDisableEmailTests(TestCase):
    @patch("engine.apps.emails.tasks.send_email_task.delay")
    def test_queues_two_fa_disable(self, mock_delay):
        user = User.objects.create_user(email="2fa@example.com", password="pass")
        queue_two_fa_disabled_email(user)
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args[0][0], TWO_FA_DISABLE)
        self.assertEqual(mock_delay.call_args[0][1], "2fa@example.com")
