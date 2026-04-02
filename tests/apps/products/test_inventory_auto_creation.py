"""Admin API: inventory rows created with products and variants."""

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from engine.apps.inventory.models import Inventory
from engine.apps.inventory.services import adjust_inventory_stock, adjust_stock
from engine.apps.inventory.utils import MAX_STOCK_QUANTITY
from engine.apps.products.models import Product, ProductVariant
from engine.apps.stores.models import StoreMembership
from engine.core.tenant_execution import tenant_scope_from_store
from tests.core.test_core import _ensure_default_plan, _make_category, _make_store, make_user


class InventoryAutoCreationTests(TestCase):
    """Product/variant admin hooks create Inventory rows; first variant drops product-level row."""

    def setUp(self):
        _ensure_default_plan()
        self.client = APIClient()
        self.store = _make_store("Inv Auto Store", "inv-auto.local")
        self.user = make_user("inv-auto-owner@example.com")
        StoreMembership.objects.create(
            user=self.user,
            store=self.store,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)
        self.category = _make_category(self.store, "InvAutoCat")

    def _store_headers(self):
        return {"HTTP_X_STORE_PUBLIC_ID": self.store.public_id}

    # ------------------------------------------------------------------
    # Case 1: Simple product gets product-level inventory
    # ------------------------------------------------------------------

    def test_admin_create_product_creates_product_level_inventory(self):
        resp = self.client.post(
            "/api/v1/admin/products/",
            {
                "name": "Simple SKU Product",
                "price": "12.00",
                "category": self.category.public_id,
                "is_active": True,
                "description": "",
            },
            format="json",
            **self._store_headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=resp.data["public_id"])
            rows = Inventory.objects.filter(product=product, variant__isnull=True)
            self.assertEqual(rows.count(), 1)
            self.assertEqual(rows.first().quantity, 0)

    # ------------------------------------------------------------------
    # Case 2: First variant transfers stock, removes product-level row
    # ------------------------------------------------------------------

    def test_first_variant_transfers_stock_from_product_level(self):
        pr = self.client.post(
            "/api/v1/admin/products/",
            {
                "name": "Stock Transfer Product",
                "price": "15.00",
                "category": self.category.public_id,
                "is_active": True,
                "description": "",
            },
            format="json",
            **self._store_headers(),
        )
        self.assertEqual(pr.status_code, status.HTTP_201_CREATED)
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=pr.data["public_id"])
            product_inv = Inventory.objects.get(product=product, variant__isnull=True)

            adjust_stock(product_inv, 25, reason="restock", source="admin")
            product_inv.refresh_from_db()
            self.assertEqual(product_inv.quantity, 25)

        product_pid = pr.data["public_id"]
        vr = self.client.post(
            "/api/v1/admin/product-variants/",
            {
                "product_public_id": product_pid,
                "attribute_value_public_ids": [],
                "is_active": True,
            },
            format="json",
            **self._store_headers(),
        )
        self.assertEqual(vr.status_code, status.HTTP_201_CREATED, vr.data)

        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=product_pid)
            self.assertEqual(
                Inventory.objects.filter(product=product, variant__isnull=True).count(),
                0,
                "Product-level inventory must be removed after first variant",
            )
            variant = ProductVariant.objects.get(public_id=vr.data["public_id"])
            self.assertTrue(variant.sku.startswith("SKU-"), variant.sku)
            variant_inv = Inventory.objects.get(product=product, variant=variant)
            self.assertEqual(
                variant_inv.quantity,
                25,
                "First variant must inherit the product-level stock quantity",
            )

    def test_first_variant_zero_stock_when_no_product_level_inventory(self):
        pr = self.client.post(
            "/api/v1/admin/products/",
            {
                "name": "No Existing Inv Product",
                "price": "10.00",
                "category": self.category.public_id,
                "is_active": True,
                "description": "",
            },
            format="json",
            **self._store_headers(),
        )
        product_pid = pr.data["public_id"]
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=product_pid)
            Inventory.objects.filter(product=product, variant__isnull=True).delete()

        vr = self.client.post(
            "/api/v1/admin/product-variants/",
            {
                "product_public_id": product_pid,
                "attribute_value_public_ids": [],
                "is_active": True,
            },
            format="json",
            **self._store_headers(),
        )
        self.assertEqual(vr.status_code, status.HTTP_201_CREATED)
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=product_pid)
            variant = ProductVariant.objects.get(public_id=vr.data["public_id"])
            self.assertEqual(
                Inventory.objects.get(product=product, variant=variant).quantity,
                0,
            )

    # ------------------------------------------------------------------
    # Case 3: Multiple variants each get their own row
    # ------------------------------------------------------------------

    def test_second_variant_second_inventory_row(self):
        pr = self.client.post(
            "/api/v1/admin/products/",
            {
                "name": "Two Variant Product",
                "price": "20.00",
                "category": self.category.public_id,
                "is_active": True,
                "description": "",
            },
            format="json",
            **self._store_headers(),
        )
        product_pid = pr.data["public_id"]
        for _ in range(2):
            r = self.client.post(
                "/api/v1/admin/product-variants/",
                {
                    "product_public_id": product_pid,
                    "attribute_value_public_ids": [],
                    "is_active": True,
                },
                format="json",
                **self._store_headers(),
            )
            self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=product_pid)
            self.assertEqual(
                Inventory.objects.filter(product=product, variant__isnull=True).count(),
                0,
            )
            self.assertEqual(
                Inventory.objects.filter(product=product, variant__isnull=False).count(),
                2,
            )

    # ------------------------------------------------------------------
    # Case 4: No duplicate product-level rows (idempotent)
    # ------------------------------------------------------------------

    def test_product_level_inventory_get_or_create_idempotent(self):
        resp = self.client.post(
            "/api/v1/admin/products/",
            {
                "name": "Idempotent Inv",
                "price": "8.00",
                "category": self.category.public_id,
                "is_active": True,
                "description": "",
            },
            format="json",
            **self._store_headers(),
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=resp.data["public_id"])
            _, created = Inventory.objects.get_or_create(
                product=product,
                variant=None,
                defaults={"quantity": 0},
            )
            self.assertFalse(created)
            self.assertEqual(
                Inventory.objects.filter(product=product, variant__isnull=True).count(),
                1,
            )

    # ------------------------------------------------------------------
    # Case 5: Deleting last variant restores product-level inventory
    # ------------------------------------------------------------------

    def test_deleting_last_variant_restores_product_level_inventory(self):
        pr = self.client.post(
            "/api/v1/admin/products/",
            {
                "name": "Delete Recovery Product",
                "price": "18.00",
                "category": self.category.public_id,
                "is_active": True,
                "description": "",
            },
            format="json",
            **self._store_headers(),
        )
        product_pid = pr.data["public_id"]

        vr = self.client.post(
            "/api/v1/admin/product-variants/",
            {
                "product_public_id": product_pid,
                "attribute_value_public_ids": [],
                "is_active": True,
            },
            format="json",
            **self._store_headers(),
        )
        self.assertEqual(vr.status_code, status.HTTP_201_CREATED)
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=product_pid)
            variant = ProductVariant.objects.get(public_id=vr.data["public_id"])
            self.assertFalse(
                Inventory.objects.filter(product=product, variant__isnull=True).exists()
            )

        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])

        variant_pid = vr.data["public_id"]
        dr = self.client.delete(
            f"/api/v1/admin/product-variants/{variant_pid}/",
            **self._store_headers(),
        )
        self.assertEqual(dr.status_code, status.HTTP_204_NO_CONTENT, dr.data if hasattr(dr, 'data') else "")
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=product_pid)
            variant = ProductVariant.objects.filter(public_id=variant_pid).first()
            self.assertIsNone(variant)
            self.assertTrue(
                Inventory.objects.filter(product=product, variant__isnull=True).exists(),
                "Product-level inventory must be restored when last variant is deleted",
            )
            self.assertEqual(
                Inventory.objects.get(product=product, variant__isnull=True).quantity,
                0,
            )

    def test_deleting_non_last_variant_does_not_create_product_level_inventory(self):
        pr = self.client.post(
            "/api/v1/admin/products/",
            {
                "name": "Multi Var Delete Product",
                "price": "22.00",
                "category": self.category.public_id,
                "is_active": True,
                "description": "",
            },
            format="json",
            **self._store_headers(),
        )
        product_pid = pr.data["public_id"]
        variant_pids: list[str] = []
        for _ in range(2):
            r = self.client.post(
                "/api/v1/admin/product-variants/",
                {
                    "product_public_id": product_pid,
                    "attribute_value_public_ids": [],
                    "is_active": True,
                },
                format="json",
                **self._store_headers(),
            )
            self.assertEqual(r.status_code, status.HTTP_201_CREATED)
            variant_pids.append(r.data["public_id"])

        self.user.is_superuser = True
        self.user.save(update_fields=["is_superuser"])

        dr = self.client.delete(
            f"/api/v1/admin/product-variants/{variant_pids[0]}/",
            **self._store_headers(),
        )
        self.assertEqual(dr.status_code, status.HTTP_204_NO_CONTENT)
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=product_pid)
            self.assertFalse(
                Inventory.objects.filter(product=product, variant__isnull=True).exists(),
                "Product-level inventory must NOT be created when other variants still exist",
            )
            self.assertEqual(
                Inventory.objects.filter(product=product, variant__isnull=False).count(),
                1,
            )

    def test_adjust_inventory_stock_clamps_upper_bound(self):
        pr = self.client.post(
            "/api/v1/admin/products/",
            {
                "name": "Clamp Upper Product",
                "price": "30.00",
                "category": self.category.public_id,
                "is_active": True,
                "description": "",
            },
            format="json",
            **self._store_headers(),
        )
        self.assertEqual(pr.status_code, status.HTTP_201_CREATED, pr.data)
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=pr.data["public_id"])
            inv = Inventory.objects.get(product=product, variant__isnull=True)
            adjust_inventory_stock(
                store_id=self.store.id,
                product_id=product.id,
                variant_id=None,
                delta_qty=-200000,  # increase stock by 200000
                reason="restock",
                source="admin",
                allow_negative=True,
            )
            inv.refresh_from_db()
            self.assertEqual(inv.quantity, MAX_STOCK_QUANTITY)

    def test_first_variant_transfer_clamps_existing_product_level_stock(self):
        pr = self.client.post(
            "/api/v1/admin/products/",
            {
                "name": "Transfer Clamp Product",
                "price": "16.00",
                "category": self.category.public_id,
                "is_active": True,
                "description": "",
            },
            format="json",
            **self._store_headers(),
        )
        self.assertEqual(pr.status_code, status.HTTP_201_CREATED, pr.data)
        product_pid = pr.data["public_id"]
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=product_pid)
            Inventory.objects.filter(product=product, variant__isnull=True).update(quantity=200000)

        vr = self.client.post(
            "/api/v1/admin/product-variants/",
            {
                "product_public_id": product_pid,
                "attribute_value_public_ids": [],
                "is_active": True,
            },
            format="json",
            **self._store_headers(),
        )
        self.assertEqual(vr.status_code, status.HTTP_201_CREATED, vr.data)
        with tenant_scope_from_store(store=self.store, reason="test"):
            product = Product.objects.get(public_id=product_pid)
            variant = ProductVariant.objects.get(public_id=vr.data["public_id"])
            variant_inv = Inventory.objects.get(product=product, variant=variant)
            self.assertEqual(variant_inv.quantity, MAX_STOCK_QUANTITY)
