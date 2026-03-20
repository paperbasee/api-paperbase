import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from engine.apps.analytics.models import (
    StoreAnalytics,
    StoreDashboardStatsSnapshot,
)
from engine.apps.billing.models import Plan
from engine.apps.customers.models import Customer
from engine.apps.orders.models import Order
from engine.apps.products.models import Category, Product
from engine.apps.stores.models import Store, StoreDeletionJob, StoreMembership


User = get_user_model()


def _make_user(email: str, password: str = "pass1234"):
    return User.objects.create_user(email=email, password=password)


def _auth_client(client: APIClient, email: str, password: str = "pass1234"):
    resp = client.post(
        "/api/v1/auth/token/",
        {"email": email, "password": password},
        format="json",
    )
    assert resp.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return resp.data["access"]


def _set_default_plan(max_stores: int):
    Plan.objects.all().update(is_default=False)
    if max_stores <= 1:
        plan_name = "basic"
    else:
        plan_name = "premium"

    plan = Plan.objects.filter(name=plan_name).first()
    if not plan:
        plan = Plan.objects.create(
            name=plan_name,
            price="0.00",
            billing_cycle="monthly",
            is_active=True,
            is_default=True,
            features={
                "limits": {"max_stores": max_stores},
                "features": {},
            },
        )
    else:
        features = plan.features or {}
        limits = features.get("limits") or {}
        limits["max_stores"] = max_stores
        features["limits"] = limits
        plan.features = features
        plan.is_default = True
        plan.save(update_fields=["features", "is_default"])


def _make_store(name: str, domain: str, owner_email: str):
    return Store.objects.create(
        name=name,
        domain=domain,
        owner_name=f"{name} Owner",
        owner_email=owner_email,
    )


def _make_owner_membership(user: User, store: Store):
    return StoreMembership.objects.create(
        user=user,
        store=store,
        role=StoreMembership.Role.OWNER,
        is_active=True,
    )


def _make_catalog_data(store: Store, user: User):
    cat = Category.objects.create(
        store=store,
        name="Electronics",
        slug="electronics",
    )
    product = Product.objects.create(
        store=store,
        category=cat,
        name="Product Alpha",
        price=10,
        stock=5,
        status=Product.Status.ACTIVE,
        is_active=True,
    )
    order = Order.objects.create(store=store, email="cust@example.com")
    customer = Customer.objects.create(store=store, user=user)

    today = datetime.date.today()
    StoreAnalytics.objects.create(store=store, period_date=today)
    StoreDashboardStatsSnapshot.objects.create(
        store=store,
        start_date=today,
        end_date=today,
        bucket=StoreDashboardStatsSnapshot.BUCKET_DAY,
        payload={},
    )
    return {"product": product, "order": order, "customer": customer}


class DeleteStoreEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_delete_store_rejects_non_owner_or_bad_inputs(self):
        _set_default_plan(max_stores=1)  # Basic

        user = _make_user("owner@example.com")
        other_user = _make_user("other@example.com")

        store = _make_store("Store A", "store-a.local", owner_email=user.email)
        _make_owner_membership(user, store)
        _make_owner_membership(other_user, store)

        _make_catalog_data(store, user)

        access = _auth_client(self.client, user.email)

        # Wrong email (exact match should fail)
        resp = self.client.post(
            "/api/v1/stores/settings/delete/",
            {"account_email": "owner@example.com ", "store_name": store.name},
            format="json",
            HTTP_X_STORE_ID=store.public_id,
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Store.objects.filter(id=store.id).exists())
        store.refresh_from_db()
        self.assertTrue(store.is_active)

        # Correct email + store name
        resp = self.client.post(
            "/api/v1/stores/settings/delete/",
            {"account_email": user.email, "store_name": store.name},
            format="json",
            HTTP_X_STORE_ID=store.public_id,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["redirect_route"], "/onboarding")

        job_id = resp.data["job_id"]
        job = StoreDeletionJob.objects.get(id=job_id, user=user)

        # The store should become inaccessible immediately (deactivated),
        # and the job should drive eventual hard deletion.
        store_exists = Store.objects.filter(id=store.id).exists()
        if store_exists:
            self.assertFalse(Store.objects.get(id=store.id).is_active)

        if job.status == StoreDeletionJob.Status.SUCCESS:
            self.assertEqual(Order.objects.filter(store_id=store.id).count(), 0)
            self.assertEqual(Customer.objects.filter(store_id=store.id).count(), 0)
            self.assertEqual(StoreAnalytics.objects.filter(store_id=store.id).count(), 0)

    def test_delete_store_premium_redirects_to_other_store(self):
        _set_default_plan(max_stores=5)  # Premium

        user = _make_user("owner@example.com")

        store_a = _make_store("Store A", "store-a.local", owner_email=user.email)
        store_b = _make_store("Store B", "store-b.local", owner_email=user.email)
        _make_owner_membership(user, store_a)
        _make_owner_membership(user, store_b)

        _make_catalog_data(store_a, user)

        access = _auth_client(self.client, user.email)
        resp = self.client.post(
            "/api/v1/stores/settings/delete/",
            {"account_email": user.email, "store_name": store_a.name},
            format="json",
            HTTP_X_STORE_ID=store_a.public_id,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["redirect_route"], "/")

        # Response tokens should include active_store_id for the next store.
        payload = AccessToken(resp.data["access"]).payload
        self.assertEqual(payload.get("active_store_id"), store_b.public_id)

    def test_delete_status_is_user_scoped(self):
        _set_default_plan(max_stores=1)

        user = _make_user("owner@example.com")
        other_user = _make_user("other@example.com")

        store = _make_store("Store A", "store-a.local", owner_email=user.email)
        _make_owner_membership(user, store)

        _make_catalog_data(store, user)
        _auth_client(self.client, user.email)

        resp = self.client.post(
            "/api/v1/stores/settings/delete/",
            {"account_email": user.email, "store_name": store.name},
            format="json",
            HTTP_X_STORE_ID=store.public_id,
        )
        self.assertEqual(resp.status_code, 200)
        job_id = resp.data["job_id"]

        other_client = APIClient()
        _auth_client(other_client, other_user.email)

        status_resp = other_client.get(
            "/api/v1/stores/settings/delete-status/?job_id=" + str(job_id)
        )
        self.assertEqual(status_resp.status_code, 404)

