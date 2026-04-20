from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from engine.apps.billing.models import Plan
from engine.apps.billing.services import activate_subscription
from engine.apps.stores.models import Store, StoreMembership, StoreSettings
from engine.apps.stores.services import allocate_unique_store_code, normalize_store_code_base_from_name

User = get_user_model()


def _make_user(email: str, password: str = "pass1234"):
    return User.objects.create_user(email=email, password=password, is_verified=True)


def _auth_client(client: APIClient, email: str, password: str = "pass1234", store_public_id: str | None = None):
    extra = {}
    if store_public_id:
        extra["HTTP_X_STORE_PUBLIC_ID"] = store_public_id
    resp = client.post(
        "/api/v1/auth/token/",
        {"email": email, "password": password},
        format="json",
        **extra,
    )
    assert resp.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return resp.data["access"]


def _set_default_plan(*, premium_order_emails: bool = False):
    Plan.objects.all().update(is_default=False)
    plan_name = "premium" if premium_order_emails else "basic"

    plan = Plan.objects.filter(name=plan_name).first()
    if not plan:
        plan = Plan.objects.create(
            name=plan_name,
            price="0.00",
            billing_cycle="monthly",
            is_active=True,
            is_default=True,
            features={
                "limits": {"max_products": 100},
                "features": {"order_email_notifications": premium_order_emails},
            },
        )
    else:
        features = plan.features or {}
        features["limits"] = {**(features.get("limits") or {}), "max_products": 500 if premium_order_emails else 100}
        features["features"] = {
            **(features.get("features") or {}),
            "order_email_notifications": premium_order_emails,
        }
        plan.features = features
        plan.is_default = True
        plan.save(update_fields=["features", "is_default"])


def _make_store(name: str, domain: str, owner_email: str):
    base = normalize_store_code_base_from_name(name) or normalize_store_code_base_from_name(
        domain.split(".")[0]
    )
    if not base:
        base = "T"
    owner = User.objects.get(email=owner_email)
    store = Store.objects.create(
        owner=owner,
        name=name,
        code=allocate_unique_store_code(base),
        owner_name=f"{name} Owner",
        owner_email=owner_email,
    )
    StoreMembership.objects.get_or_create(
        user=owner,
        store=store,
        defaults={
            "role": StoreMembership.Role.OWNER,
            "is_active": True,
        },
    )
    return store


class StoreSettingsOrderEmailTests(TestCase):
    """Order email notification flags: premium + owner-only writes."""

    def setUp(self):
        self.client = APIClient()
        _set_default_plan(premium_order_emails=True)
        self.owner = _make_user("owner@order-email.test")
        self.staff_user = _make_user("staff@order-email.test")
        self.store = _make_store("Order Email Store", "order-email.test", self.owner.email)
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
        _auth_client(self.client, self.staff_user.email, store_public_id=self.store.public_id)
        resp = self.client.patch(
            "/api/v1/store/settings/current/",
            {"email_notify_owner_on_order_received": True},
            format="json",
            HTTP_X_STORE_PUBLIC_ID=self.store.public_id,
        )
        self.assertEqual(resp.status_code, 400)

    def test_owner_basic_cannot_enable_order_email_flags(self):
        basic = Plan.objects.filter(name="basic").first()
        if not basic:
            basic = Plan.objects.create(
                name="basic",
                price="0.00",
                billing_cycle="monthly",
                is_active=True,
                is_default=True,
                features={"limits": {"max_products": 100}, "features": {}},
            )
        activate_subscription(self.owner, basic, source="manual", amount=0, provider="manual")
        _auth_client(self.client, self.owner.email, store_public_id=self.store.public_id)
        resp = self.client.patch(
            "/api/v1/store/settings/current/",
            {"email_notify_owner_on_order_received": True},
            format="json",
            HTTP_X_STORE_PUBLIC_ID=self.store.public_id,
        )
        self.assertEqual(resp.status_code, 400)

    def test_owner_premium_can_toggle_order_email_flags(self):
        premium = Plan.objects.filter(name="premium").first()
        self.assertIsNotNone(premium)
        activate_subscription(self.owner, premium, source="manual", amount=0, provider="manual")
        _auth_client(self.client, self.owner.email, store_public_id=self.store.public_id)
        resp = self.client.patch(
            "/api/v1/store/settings/current/",
            {
                "email_notify_owner_on_order_received": True,
                "email_customer_on_order_confirmed": True,
            },
            format="json",
            HTTP_X_STORE_PUBLIC_ID=self.store.public_id,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["email_notify_owner_on_order_received"])
        self.assertTrue(resp.data["email_customer_on_order_confirmed"])
