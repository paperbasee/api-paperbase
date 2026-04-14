"""EmailLog store linkage, metadata redaction, and send_email integration."""

import uuid as _uuid
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from engine.apps.emails.models import EmailLog
from engine.apps.emails.services import (
    resolve_email_log_store,
    sanitize_email_metadata_for_storage,
    send_email,
)
from engine.apps.orders.models import Order
from engine.apps.shipping.models import ShippingZone
from engine.apps.stores.models import Store
from tests.apps.emails.test_triggers import _store


def _order(store: Store, **kwargs):
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


class ResolveEmailLogStoreTests(TestCase):
    def test_from_store_public_id(self):
        store = _store()
        self.assertEqual(
            resolve_email_log_store({"store_public_id": store.public_id}),
            store,
        )

    def test_from_store_instance(self):
        store = _store()
        self.assertEqual(resolve_email_log_store({"store": store}), store)

    def test_from_order_instance(self):
        store = _store()
        order = _order(store)
        self.assertEqual(resolve_email_log_store({"order": order}), store)

    def test_from_store_id(self):
        store = _store()
        self.assertEqual(resolve_email_log_store({"store_id": store.pk}), store)


class SanitizeMetadataTests(TestCase):
    def test_redacts_known_pii_keys(self):
        raw = {
            "phone": "017",
            "shipping_address": "secret st",
            "order_summary": "full blob",
            "total": "100.00",
            "items_lines": ["- Shirt x1"],
        }
        out = sanitize_email_metadata_for_storage(raw)
        self.assertEqual(out["phone"], "[REDACTED]")
        self.assertEqual(out["shipping_address"], "[REDACTED]")
        self.assertEqual(out["order_summary"], "[REDACTED]")
        self.assertEqual(out["total"], "100.00")
        self.assertEqual(out["items_lines"], ["- Shirt x1"])

    def test_redacts_html_like_body(self):
        raw = {"title": "Hi", "body": "<html><body>x</body></html>"}
        out = sanitize_email_metadata_for_storage(raw)
        self.assertEqual(out["title"], "Hi")
        self.assertEqual(out["body"], "[REDACTED]")


class SendEmailLogIntegrationTests(TestCase):
    @patch("engine.apps.emails.services.get_email_provider")
    def test_emaillog_has_store_and_redacted_metadata(self, _mock_provider):
        store = _store()
        ctx = {
            "store_public_id": store.public_id,
            "store_name": store.name,
            "phone": "01711111111",
            "shipping_address": "Hidden Rd",
            "order_summary": "PII block",
            "total": "50.00",
        }
        send_email("ORDER_RECEIVED", "to@example.com", ctx)
        log = EmailLog.objects.latest("created_at")
        self.assertEqual(log.store_id, store.id)
        self.assertEqual(log.metadata.get("phone"), "[REDACTED]")
        self.assertEqual(log.metadata.get("shipping_address"), "[REDACTED]")
        self.assertEqual(log.metadata.get("order_summary"), "[REDACTED]")
        self.assertEqual(log.metadata.get("total"), "50.00")


class EmailLogAdminQuerysetTests(TestCase):
    def test_non_superuser_excludes_other_store_logs(self):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.auth import get_user_model
        from django.test import RequestFactory

        from engine.apps.emails.admin import EmailLogAdmin

        User = get_user_model()
        store_a = _store()
        store_b = _store()
        owner_a = store_a.owner
        owner_a.is_staff = True
        owner_a.save()

        EmailLog.objects.create(
            to_email="a@example.com",
            type="ORDER_RECEIVED",
            status=EmailLog.Status.SENT,
            provider="resend",
            metadata={"x": 1},
            store=store_a,
        )
        EmailLog.objects.create(
            to_email="b@example.com",
            type="ORDER_RECEIVED",
            status=EmailLog.Status.SENT,
            provider="resend",
            metadata={"x": 2},
            store=store_b,
        )

        factory = RequestFactory()
        request = factory.get("/admin/emails/emaillog/")
        request.user = owner_a

        site = AdminSite()
        ma = EmailLogAdmin(EmailLog, site)
        qs = ma.get_queryset(request)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().store_id, store_a.id)

    def test_superuser_sees_all_logs(self):
        from django.contrib.admin.sites import AdminSite
        from django.contrib.auth import get_user_model
        from django.test import RequestFactory

        from engine.apps.emails.admin import EmailLogAdmin

        su = get_user_model().objects.create_superuser(
            email="su@emaillog.admin.test",
            password="secret123",
        )
        store_a = _store()
        store_b = _store()
        EmailLog.objects.create(
            to_email="a@example.com",
            type="ORDER_RECEIVED",
            status=EmailLog.Status.SENT,
            provider="resend",
            metadata={},
            store=store_a,
        )
        EmailLog.objects.create(
            to_email="b@example.com",
            type="ORDER_RECEIVED",
            status=EmailLog.Status.SENT,
            provider="resend",
            metadata={},
            store=store_b,
        )

        factory = RequestFactory()
        request = factory.get("/admin/")
        request.user = su

        site = AdminSite()
        ma = EmailLogAdmin(EmailLog, site)
        self.assertEqual(ma.get_queryset(request).count(), 2)

    def test_metadata_field_hidden_for_non_superuser(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory

        from engine.apps.emails.admin import EmailLogAdmin

        store = _store()
        owner = store.owner
        owner.is_staff = True
        owner.save()

        factory = RequestFactory()
        request = factory.get("/admin/")
        request.user = owner

        ma = EmailLogAdmin(EmailLog, AdminSite())
        self.assertNotIn("metadata", ma.get_fields(request))

        su = owner.__class__.objects.create_superuser(
            email="su2@emaillog.admin.test",
            password="secret123",
        )
        request.user = su
        self.assertIn("metadata", ma.get_fields(request))
