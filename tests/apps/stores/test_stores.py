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
from engine.apps.billing.services import activate_subscription
from engine.apps.customers.models import Customer
from engine.apps.orders.models import Order
from engine.apps.shipping.models import ShippingZone
from engine.apps.products.models import Category, Product
from engine.apps.stores.models import Domain, Store, StoreDeletionJob, StoreMembership, StoreSettings


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
    store = Store.objects.create(
        name=name,
        domain=None,
        owner_name=f"{name} Owner",
        owner_email=owner_email,
    )
    if domain:
        Domain.objects.filter(store=store, is_custom=False).update(
            domain=domain.strip().lower().split(":", 1)[0]
        )
    return store


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
    zone = ShippingZone.objects.create(store=store, name="Store Zone", is_active=True)
    order = Order.objects.create(store=store, email="cust@example.com", shipping_zone=zone)
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
        job = StoreDeletionJob.objects.get(public_id=job_id, user=user)

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

        # Response tokens should include active_store_public_id for the next store.
        payload = AccessToken(resp.data["access"]).payload
        self.assertEqual(payload.get("active_store_public_id"), store_b.public_id)

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


class StoreSettingsOrderEmailTests(TestCase):
    """Order email notification flags: premium + owner-only writes."""

    def setUp(self):
        self.client = APIClient()
        _set_default_plan(max_stores=5)
        self.owner = _make_user("owner@order-email.test")
        self.staff_user = _make_user("staff@order-email.test")
        self.store = _make_store("Order Email Store", "order-email.test", self.owner.email)
        _make_owner_membership(self.owner, self.store)
        StoreMembership.objects.create(
            user=self.staff_user,
            store=self.store,
            role=StoreMembership.Role.STAFF,
            is_active=True,
        )
        StoreSettings.objects.get_or_create(store=self.store)

    def test_staff_cannot_patch_order_email_flags(self):
        premium = Plan.objects.filter(name="premium").first()
        self.assertIsNotNone(premium)
        activate_subscription(self.owner, premium, source="manual", amount=0, provider="manual")
        _auth_client(self.client, self.staff_user.email)
        resp = self.client.patch(
            "/api/v1/stores/settings/current/",
            {"email_notify_owner_on_order_received": True},
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
        )
        self.assertEqual(resp.status_code, 400)

    def test_owner_basic_cannot_enable_order_email_flags(self):
        basic = Plan.objects.filter(name="basic").first()
        self.assertIsNotNone(basic)
        activate_subscription(self.owner, basic, source="manual", amount=0, provider="manual")
        _auth_client(self.client, self.owner.email)
        resp = self.client.patch(
            "/api/v1/stores/settings/current/",
            {"email_notify_owner_on_order_received": True},
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
        )
        self.assertEqual(resp.status_code, 400)

    def test_owner_premium_can_toggle_order_email_flags(self):
        premium = Plan.objects.filter(name="premium").first()
        self.assertIsNotNone(premium)
        activate_subscription(self.owner, premium, source="manual", amount=0, provider="manual")
        _auth_client(self.client, self.owner.email)
        resp = self.client.patch(
            "/api/v1/stores/settings/current/",
            {
                "email_notify_owner_on_order_received": True,
                "email_customer_on_order_confirmed": True,
            },
            format="json",
            HTTP_X_STORE_ID=self.store.public_id,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["email_notify_owner_on_order_received"])
        self.assertTrue(resp.data["email_customer_on_order_confirmed"])

