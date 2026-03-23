from django.test import TestCase
from rest_framework.test import APIClient

from engine.apps.stores.models import Domain, Store, StoreMembership
from engine.apps.products.models import Category, Product
from engine.apps.notifications.models import StorefrontCTA

from django.contrib.auth import get_user_model

User = get_user_model()


def make_user(email, password="pass1234", **kwargs):
    return User.objects.create_user(email=email, password=password, **kwargs)


def make_store(name, domain):
    store = Store.objects.create(
        name=name,
        domain=None,
        owner_name=f"{name} Owner",
        owner_email=f"owner@{domain}",
    )
    Domain.objects.filter(store=store, is_custom=False).update(
        domain=domain.strip().lower().split(":", 1)[0]
    )
    return store


def make_membership(user, store, role=StoreMembership.Role.OWNER):
    return StoreMembership.objects.create(user=user, store=store, role=role)


def make_category(store, name="Cat", slug=None):
    return Category.objects.create(
        store=store, name=name, slug=slug or name.lower().replace(" ", "-")
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


class CrossTenantProductIsolationTests(TestCase):
    """
    Verify that all product-related storefront endpoints are strictly scoped to
    the active store and cannot leak data across tenant boundaries.
    """

    def setUp(self):
        self.client = APIClient()

        self.store_a = make_store("Store A", "store-a.local")
        self.store_b = make_store("Store B", "store-b.local")

        self.user_a = make_user("user-a@store-a.local")
        self.user_b = make_user("user-b@store-b.local")

        make_membership(self.user_a, self.store_a)
        make_membership(self.user_b, self.store_b)

        self.cat_a = make_category(self.store_a, "Electronics", "electronics")
        self.cat_b = make_category(self.store_b, "Electronics", "electronics")

        self.product_a = make_product(self.store_a, self.cat_a, name="Product Alpha")
        self.product_b = make_product(self.store_b, self.cat_b, name="Product Beta")

        self.notif_a = StorefrontCTA.objects.create(
            store=self.store_a, cta_text="Banner Store A", is_active=True
        )
        self.notif_b = StorefrontCTA.objects.create(
            store=self.store_b, cta_text="Banner Store B", is_active=True
        )

    def tearDown(self):
        from engine.core.domain_resolution_cache import invalidate_domain_host

        invalidate_domain_host("store-a.local")
        invalidate_domain_host("store-b.local")
        super().tearDown()

    # ------------------------------------------------------------------
    # 1.1 / 1.3  Product list endpoint
    # ------------------------------------------------------------------

    def test_product_list_returns_only_current_store(self):
        """GET /products/ on store A's host must never include store B's products."""
        resp = self.client.get("/api/v1/products/", HTTP_HOST="store-a.local")
        self.assertEqual(resp.status_code, 200)
        ids = [item["public_id"] for item in resp.data.get("results", resp.data)]
        self.assertIn(self.product_a.public_id, ids)
        self.assertNotIn(self.product_b.public_id, ids)

    def test_product_list_on_store_b_excludes_store_a(self):
        """GET /products/ on store B's host must never include store A's products."""
        resp = self.client.get("/api/v1/products/", HTTP_HOST="store-b.local")
        self.assertEqual(resp.status_code, 200)
        ids = [item["public_id"] for item in resp.data.get("results", resp.data)]
        self.assertIn(self.product_b.public_id, ids)
        self.assertNotIn(self.product_a.public_id, ids)

    # ------------------------------------------------------------------
    # 1.1  Direct product detail cross-store access
    # ------------------------------------------------------------------

    def test_product_detail_cross_store_access_denied(self):
        """GET /products/{uuid}/ for store B's product on store A's host must return 404."""
        resp = self.client.get(
            f"/api/v1/products/{self.product_b.id}/",
            HTTP_HOST="store-a.local",
        )
        self.assertEqual(
            resp.status_code,
            404,
            "Store A must not be able to retrieve Store B's product by UUID",
        )

    def test_product_detail_cross_store_access_denied_by_slug(self):
        """GET /products/{slug}/ for store B's product on store A's host must return 404."""
        resp = self.client.get(
            f"/api/v1/products/{self.product_b.slug}/",
            HTTP_HOST="store-a.local",
        )
        self.assertEqual(
            resp.status_code,
            404,
            "Store A must not be able to retrieve Store B's product by slug",
        )

    # ------------------------------------------------------------------
    # Critical: ProductSearchView — was missing store filter
    # ------------------------------------------------------------------

    def test_product_search_scoped_to_current_store(self):
        """
        GET /products/search/?q=... on store A's host must not return store B's products.
        This validates the fix for the Critical vulnerability in ProductSearchView.
        """
        resp = self.client.get(
            "/api/v1/products/search/?q=Product",
            HTTP_HOST="store-a.local",
        )
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        ids = [item["public_id"] for item in results]
        self.assertNotIn(
            self.product_b.public_id,
            ids,
            "ProductSearchView must not return products from another store",
        )

    def test_product_search_only_returns_own_store_results(self):
        """Search on store A must return store A's products and exclude store B's."""
        resp = self.client.get(
            "/api/v1/products/search/?q=Alpha",
            HTTP_HOST="store-a.local",
        )
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        ids = [item["public_id"] for item in results]
        self.assertIn(self.product_a.public_id, ids)
        self.assertNotIn(self.product_b.public_id, ids)

    def test_product_search_without_store_context_forbidden(self):
        """Search with no store context must not return tenant data (403)."""
        resp = self.client.get("/api/v1/products/search/?q=Product")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("detail"), "Unknown tenant host.")

    # ------------------------------------------------------------------
    # Active notifications (CTA banners) — store-scoped public list
    # ------------------------------------------------------------------

    def test_active_notifications_scoped_to_store_a_host(self):
        resp = self.client.get("/api/v1/notifications/active/", HTTP_HOST="store-a.local")
        self.assertEqual(resp.status_code, 200)
        payload = resp.data.get("results", resp.data)
        texts = [item["text"] for item in payload]
        self.assertIn("Banner Store A", texts)
        self.assertNotIn("Banner Store B", texts)

    def test_active_notifications_scoped_to_store_b_host(self):
        resp = self.client.get("/api/v1/notifications/active/", HTTP_HOST="store-b.local")
        self.assertEqual(resp.status_code, 200)
        payload = resp.data.get("results", resp.data)
        texts = [item["text"] for item in payload]
        self.assertIn("Banner Store B", texts)
        self.assertNotIn("Banner Store A", texts)

    def test_active_notifications_without_store_context_forbidden(self):
        resp = self.client.get("/api/v1/notifications/active/")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("detail"), "Unknown tenant host.")

    # ------------------------------------------------------------------
    # Critical: ProductRelatedView — was missing store filter
    # ------------------------------------------------------------------

    def test_related_products_scoped_to_current_store(self):
        """
        GET /products/{id}/related/ must not include products from store B.
        Both stores share the same category slug, so without store scoping
        store B's product would appear in store A's related results.
        """
        resp = self.client.get(
            f"/api/v1/products/{self.product_a.public_id}/related/",
            HTTP_HOST="store-a.local",
        )
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        ids = [item["public_id"] for item in results]
        self.assertNotIn(
            self.product_b.public_id,
            ids,
            "ProductRelatedView must not return products from another store",
        )

    # ------------------------------------------------------------------
    # Category isolation
    # ------------------------------------------------------------------

    def test_category_list_scoped_to_current_store(self):
        """GET /categories/ must only return categories from the active store."""
        resp = self.client.get("/api/v1/categories/", HTTP_HOST="store-a.local")
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", resp.data)
        public_ids = [item["public_id"] for item in results]
        self.assertIn(self.cat_a.public_id, public_ids)
        self.assertNotIn(self.cat_b.public_id, public_ids)
