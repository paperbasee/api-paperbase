"""Trash / soft-delete: tenant isolation, superuser hard delete, restore, purge."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from engine.apps.inventory.models import Inventory
from engine.apps.inventory.utils import MAX_STOCK_QUANTITY, MIN_STOCK_QUANTITY
from engine.apps.products.models import Product
from engine.apps.stores.models import StoreMembership
from engine.core.models import TrashItem
from engine.core.tenant_execution import tenant_scope_from_store
from engine.core.trash_service import purge_expired_trash

from .test_core import (
    _ensure_default_plan,
    _make_category,
    _make_membership,
    _make_order,
    _make_product,
    _make_store,
    make_user,
)
from tests.test_helpers.jwt_auth import login_dashboard_jwt


class TrashSoftDeleteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        _ensure_default_plan()
        self.owner = make_user("trash-owner@example.com")
        self.store = _make_store("Trash Store", "trash-store.local", owner_email=self.owner.email)
        self.cat = _make_category(self.store, "Trash Cat")
        self.product = _make_product(self.store, self.cat, name="Trash Product")

    def _auth_owner(self):
        login_dashboard_jwt(self.client, self.owner.email)

    def test_store_admin_delete_creates_trash_and_removes_product(self):
        self._auth_owner()
        pid = self.product.public_id
        resp = self.client.delete(f"/api/v1/admin/products/{pid}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        with tenant_scope_from_store(store=self.store, reason="test assert product absent"):
            self.assertFalse(Product.objects.filter(public_id=pid).exists())
        self.assertEqual(
            TrashItem.objects.filter(store=self.store, entity_type=TrashItem.EntityType.PRODUCT).count(),
            1,
        )
        tr = TrashItem.objects.get(store=self.store, entity_type=TrashItem.EntityType.PRODUCT)
        self.assertFalse(tr.is_restored)
        self.assertGreater(tr.expires_at, tr.deleted_at)

    def test_superuser_delete_does_not_create_trash(self):
        su = make_user("trash-su@example.com", is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=su)
        self.client.credentials(HTTP_X_STORE_PUBLIC_ID=self.store.public_id)
        pid = self.product.public_id
        resp = self.client.delete(f"/api/v1/admin/products/{pid}/")
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(TrashItem.objects.filter(store=self.store).count(), 0)

    def test_staff_cannot_access_trash(self):
        staff = make_user("trash-staff@example.com")
        _make_membership(staff, self.store, StoreMembership.Role.STAFF)
        login_dashboard_jwt(self.client, staff.email)
        resp = self.client.get("/api/v1/admin/trash/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_trash_list_scoped_to_store(self):
        self._auth_owner()
        self.client.delete(f"/api/v1/admin/products/{self.product.public_id}/")
        resp = self.client.get("/api/v1/admin/trash/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["entity_type"], TrashItem.EntityType.PRODUCT)
        self.assertEqual(results[0]["entity_name"], "Trash Product")

    def test_cannot_access_other_store_trash_by_id(self):
        self._auth_owner()
        self.client.delete(f"/api/v1/admin/products/{self.product.public_id}/")
        tid = TrashItem.objects.get(store=self.store).pk

        other_owner = make_user("other-trash@example.com")
        other = _make_store("Other Trash", "other-trash.local", owner_email=other_owner.email)
        login_dashboard_jwt(self.client, other_owner.email)
        resp = self.client.get(f"/api/v1/admin/trash/{tid}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_restore_product_roundtrip(self):
        self._auth_owner()
        pub = self.product.public_id
        self.client.delete(f"/api/v1/admin/products/{pub}/")
        tid = TrashItem.objects.get(store=self.store).pk
        resp = self.client.post(f"/api/v1/admin/trash/{tid}/restore/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        with tenant_scope_from_store(store=self.store, reason="test assert product restored"):
            self.assertTrue(Product.objects.filter(public_id=pub).exists())
        tr = TrashItem.objects.get(pk=tid)
        self.assertTrue(tr.is_restored)

    def test_restore_product_clamps_snapshot_inventory_quantity(self):
        self._auth_owner()
        pub = self.product.public_id
        self.client.delete(f"/api/v1/admin/products/{pub}/")
        tr = TrashItem.objects.get(store=self.store, entity_type=TrashItem.EntityType.PRODUCT)
        snap = tr.snapshot_json
        inventories = list(snap.get("inventories") or [])
        self.assertTrue(inventories, "Expected product snapshot to include inventory rows")
        inventories[0]["quantity"] = 999999
        snap["inventories"] = inventories
        snap["product"]["stock"] = -123
        tr.snapshot_json = snap
        tr.save(update_fields=["snapshot_json"])

        resp = self.client.post(f"/api/v1/admin/trash/{tr.pk}/restore/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        with tenant_scope_from_store(store=self.store, reason="test assert product restored clamped"):
            product = Product.objects.get(public_id=pub)
            inv = Inventory.objects.get(product=product, variant__isnull=True)
            self.assertEqual(inv.quantity, MAX_STOCK_QUANTITY)
            self.assertEqual(product.stock, MIN_STOCK_QUANTITY)

    def test_purge_expired_deletes_trash_row(self):
        self._auth_owner()
        self.client.delete(f"/api/v1/admin/products/{self.product.public_id}/")
        tr = TrashItem.objects.get(store=self.store)
        TrashItem.objects.filter(pk=tr.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        n = purge_expired_trash()
        self.assertGreaterEqual(n, 1)
        self.assertFalse(TrashItem.objects.filter(pk=tr.pk).exists())


class TrashOrderSoftDeleteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        _ensure_default_plan()
        self.owner = make_user("trash-order-owner@example.com")
        self.store = _make_store("Trash Order Store", "trash-order.local", owner_email=self.owner.email)
        self.order = _make_order(self.store)

    def _auth(self):
        login_dashboard_jwt(self.client, self.owner.email)

    def test_order_delete_returns_405(self):
        self._auth()
        oid = self.order.public_id
        resp = self.client.delete(f"/api/v1/admin/orders/{oid}/")
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(
            TrashItem.objects.filter(store=self.store, entity_type=TrashItem.EntityType.ORDER).count(),
            0,
        )

    def test_superuser_order_delete_returns_405(self):
        su = make_user("trash-order-su@example.com", is_staff=True, is_superuser=True)
        self.client.force_authenticate(user=su)
        self.client.credentials(HTTP_X_STORE_PUBLIC_ID=self.store.public_id)
        oid = self.order.public_id
        resp = self.client.delete(f"/api/v1/admin/orders/{oid}/")
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(TrashItem.objects.filter(store=self.store).count(), 0)

    def test_trash_endpoints_cannot_restore_or_permanently_delete_orders(self):
        self._auth()
        tr = TrashItem.objects.create(
            store=self.store,
            entity_type=TrashItem.EntityType.ORDER,
            entity_id=str(self.order.pk),
            entity_public_id=self.order.public_id,
            snapshot_json={"schema_version": 1, "order": {"id": str(self.order.pk)}},
            deleted_by=self.owner,
            expires_at=timezone.now() + timedelta(days=1),
        )

        resp = self.client.get("/api/v1/admin/trash/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data.get("results", resp.data)
        self.assertEqual(len(results), 0)

        resp = self.client.get(f"/api/v1/admin/trash/{tr.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

        resp = self.client.post(f"/api/v1/admin/trash/{tr.pk}/restore/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

        resp = self.client.delete(f"/api/v1/admin/trash/{tr.pk}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
