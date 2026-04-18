"""Order CSV export API and multitenant guards."""

import csv
import re
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from engine.apps.orders.export_cleanup import cleanup_expired_order_exports
from engine.apps.orders.export_csv_format import ORDER_CSV_HEADERS
from engine.apps.orders.export_tasks import run_order_export_csv_job
from engine.apps.orders.models import Order, OrderExportJob

from tests.core.test_core import (
    _default_shipping_zone,
    _ensure_default_plan,
    _make_order,
    _make_store,
    make_user,
)
from tests.test_helpers.jwt_auth import login_dashboard_jwt


class OrderExportApiTests(TestCase):
    def setUp(self):
        _ensure_default_plan()
        self.client = APIClient()
        self.owner = make_user("order-export-owner@example.com")
        self.store_a = _make_store(
            "Export Store A",
            "export-a.local",
            owner_email=self.owner.email,
        )
        self.zone_a = _default_shipping_zone(self.store_a)
        self.order_a = _make_order(
            self.store_a,
            "ea@example.com",
            shipping_zone=self.zone_a,
            district="Dhaka",
            shipping_address="Line 1",
        )

        self.owner_b = make_user("order-export-owner-b@example.com")
        self.store_b = _make_store(
            "Export Store B",
            "export-b.local",
            owner_email=self.owner_b.email,
        )
        self.zone_b = _default_shipping_zone(self.store_b)
        self.order_b = _make_order(
            self.store_b,
            "eb@example.com",
            shipping_zone=self.zone_b,
            district="Dhaka",
            shipping_address="Line 2",
        )

    def _auth(self, user):
        login_dashboard_jwt(self.client, user.email)

    def test_create_select_all_and_poll(self):
        self._auth(self.owner)
        r = self.client.post(
            "/api/v1/admin/orders/export/",
            {"select_all": True, "filters": {"status": Order.Status.PENDING}},
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        job_id = r.data["job_id"]
        self.assertEqual(r.data["status"], "PENDING")

        with patch(
            "engine.apps.orders.export_tasks.default_storage.save",
            return_value="tenants/str_x/exports/order_str_x_2020-01-01__00000000-0000-0000-0000-000000000000.csv",
        ):
            run_order_export_csv_job(job_id)

        job = OrderExportJob.objects.get(id=job_id)
        self.assertEqual(job.store_id, self.store_a.id)
        self.assertEqual(job.status, OrderExportJob.Status.COMPLETED)
        self.assertEqual(job.progress, 100)
        self.assertTrue(job.file_path)

        r2 = self.client.get(f"/api/v1/admin/orders/export/{job_id}/")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["status"], "COMPLETED")
        self.assertIsNotNone(r2.data.get("download_url"))

    def test_create_rejects_foreign_order_ids(self):
        self._auth(self.owner)
        r = self.client.post(
            "/api/v1/admin/orders/export/",
            {
                "select_all": False,
                "order_ids": [self.order_b.public_id],
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_poll_isolated_by_store(self):
        job = OrderExportJob.objects.create(
            store=self.store_b,
            user=self.owner_b,
            status=OrderExportJob.Status.PENDING,
            select_all=False,
            filters={},
            selected_order_ids=[self.order_b.public_id],
        )
        self._auth(self.owner)
        r = self.client.get(f"/api/v1/admin/orders/export/{job.id}/")
        self.assertEqual(r.status_code, 404)

    def test_download_gone_when_expired(self):
        job = OrderExportJob.objects.create(
            store=self.store_a,
            user=self.owner,
            status=OrderExportJob.Status.COMPLETED,
            select_all=True,
            filters={},
            file_path="tenants/str_x/exports/order_str_x_2020-01-01__00000000-0000-0000-0000-000000000000.csv",
            progress=100,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self._auth(self.owner)
        r = self.client.get(f"/api/v1/admin/orders/export/{job.id}/download/")
        self.assertEqual(r.status_code, 410)

    @patch("engine.apps.orders.export_cleanup.default_storage.delete")
    def test_cleanup_marks_expired(self, mock_delete):
        legacy_path = "tenants/str_x/exports/order_str_x_2020-01-01__00000000-0000-0000-0000-000000000001.csv"
        job = OrderExportJob.objects.create(
            store=self.store_a,
            user=self.owner,
            status=OrderExportJob.Status.COMPLETED,
            select_all=True,
            filters={},
            file_path=legacy_path,
            progress=100,
            expires_at=timezone.now() - timedelta(hours=2),
        )
        cleanup_expired_order_exports()
        job.refresh_from_db()
        self.assertEqual(job.status, OrderExportJob.Status.EXPIRED)
        self.assertEqual(job.file_path, "")
        mock_delete.assert_called_once_with(legacy_path)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class OrderExportTaskIntegrationTests(TestCase):
    def setUp(self):
        _ensure_default_plan()
        self.owner = make_user("order-export-task@example.com")
        self.store = _make_store(
            "Export Task Store",
            "export-task.local",
            owner_email=self.owner.email,
        )
        self.zone = _default_shipping_zone(self.store)
        self.order = _make_order(
            self.store,
            "task@example.com",
            shipping_zone=self.zone,
            district="Dhaka",
            shipping_address="House 12, Road 5",
        )
        Order.objects.filter(pk=self.order.pk).update(phone="0123456789")

    def test_eager_task_writes_csv_to_storage(self):
        job = OrderExportJob.objects.create(
            store=self.store,
            user=self.owner,
            status=OrderExportJob.Status.PENDING,
            select_all=False,
            filters={},
            selected_order_ids=[self.order.public_id],
        )
        buf = StringIO()

        def fake_save(name, content, max_length=None):
            data = content.read()
            buf.write(data.decode("utf-8"))
            return name

        with patch("engine.apps.orders.export_tasks.default_storage.save", side_effect=fake_save):
            run_order_export_csv_job(str(job.id))

        job.refresh_from_db()
        self.assertEqual(job.status, OrderExportJob.Status.COMPLETED)
        self.assertTrue(
            (job.file_path or "").startswith(f"tenants/{self.store.public_id}/exports/"),
            job.file_path,
        )
        body = buf.getvalue()
        reader = csv.reader(StringIO(body))
        header = next(reader)
        self.assertEqual(header, ORDER_CSV_HEADERS)
        row = next(reader)
        row_by_key = dict(zip(ORDER_CSV_HEADERS, row))
        self.assertEqual(row_by_key["order_id"], self.order.public_id)
        self.assertIn("0123456789", row_by_key["phone"])
        self.assertTrue(row_by_key["phone"].startswith("\t"))
        self.assertIn("Dhaka", row_by_key["full_address"])
        self.assertIn("House 12", row_by_key["full_address"])
        self.assertRegex(
            row_by_key["created_at"],
            re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"),
        )
        self.assertNotIn("T", row_by_key["created_at"])
        self.assertNotIn("+", row_by_key["created_at"])
