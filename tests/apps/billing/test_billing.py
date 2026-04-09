"""Billing app tests."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from engine.apps.billing.feature_gate import (
    get_feature_config,
    get_limit,
    has_feature,
    require_feature,
)
from engine.apps.billing.models import Payment, Plan, Subscription
from engine.apps.billing.services import activate_subscription, extend_subscription, get_active_subscription
from engine.apps.stores.models import Store, StoreApiKey, StoreMembership, StoreSettings
from engine.apps.stores.services import allocate_unique_store_code

User = get_user_model()


def _plan_features(limits=None, features=None):
    return {
        "limits": limits or {"max_products": 100},
        "features": features or {"basic_analytics": False, "marketing_tools": False},
    }


class BillingServicesTests(TestCase):
    def setUp(self):
        self.plan_basic = Plan.objects.filter(is_default=True).first()
        if not self.plan_basic:
            self.plan_basic = Plan.objects.create(
                name="basic",
                price=0,
                billing_cycle="monthly",
                features=_plan_features(limits={"max_products": 100}),
                is_default=True,
                is_active=True,
            )
        self.plan_premium = Plan.objects.filter(name="premium").first()
        if not self.plan_premium:
            self.plan_premium = Plan.objects.create(
                name="premium",
                price=999,
                billing_cycle="monthly",
                features=_plan_features(
                    limits={"max_products": 500},
                    features={"basic_analytics": True, "marketing_tools": True},
                ),
                is_active=True,
            )
        self.user = User.objects.create_user(
            username="billinguser",
            email="b@example.com",
            password="pass",
            is_verified=True,
        )

    def test_get_active_subscription_returns_none_when_no_subscription(self):
        self.assertIsNone(get_active_subscription(self.user))

    def test_activate_subscription_creates_subscription_and_payment(self):
        sub = activate_subscription(
            self.user,
            self.plan_basic,
            billing_cycle="monthly",
            duration_days=30,
            source="manual",
            amount=0,
            provider="manual",
        )
        self.assertIsNotNone(sub)
        self.assertEqual(sub.user, self.user)
        self.assertEqual(sub.plan, self.plan_basic)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertEqual(sub.source, Subscription.Source.MANUAL)
        self.assertEqual(sub.payments.count(), 1)
        self.assertEqual(sub.payments.first().status, "success")

    def test_activate_subscription_reuses_pending_payment_no_duplicate_row(self):
        pending = Payment.objects.create(
            user=self.user,
            plan=self.plan_premium,
            subscription=None,
            amount=self.plan_premium.price,
            currency="BDT",
            status=Payment.Status.PENDING,
            provider=Payment.Provider.MANUAL,
            transaction_id="TXN-REUSE-TEST-001",
            metadata={},
        )
        before_count = Payment.objects.filter(user=self.user).count()
        sub = activate_subscription(
            self.user,
            self.plan_premium,
            billing_cycle="monthly",
            duration_days=30,
            source="payment",
            amount=pending.amount,
            provider=pending.provider,
            existing_pending_payment=pending,
        )
        pending.refresh_from_db()
        self.assertEqual(Payment.objects.filter(user=self.user).count(), before_count)
        self.assertEqual(pending.subscription_id, sub.id)
        self.assertEqual(pending.status, Payment.Status.SUCCESS)
        self.assertEqual(pending.transaction_id, "TXN-REUSE-TEST-001")
        self.assertEqual(sub.payments.get().id, pending.id)

    def test_activate_subscription_expires_previous(self):
        activate_subscription(self.user, self.plan_basic, source="manual", amount=0, provider="manual")
        activate_subscription(self.user, self.plan_premium, source="manual", amount=999, provider="manual")
        active = get_active_subscription(self.user)
        self.assertIsNotNone(active)
        self.assertEqual(active.plan, self.plan_premium)
        expired = Subscription.objects.filter(user=self.user, status=Subscription.Status.EXPIRED)
        self.assertEqual(expired.count(), 1)

    def test_extend_subscription_adds_days(self):
        sub = activate_subscription(self.user, self.plan_basic, source="manual", amount=0, provider="manual")
        original_end = sub.end_date
        extend_subscription(sub, days=14)
        sub.refresh_from_db()
        self.assertEqual((sub.end_date - original_end).days, 14)

    def test_extend_subscription_rejects_canceled(self):
        sub = activate_subscription(self.user, self.plan_basic, source="manual", amount=0, provider="manual")
        sub.status = Subscription.Status.CANCELED
        sub.save()
        with self.assertRaises(ValueError):
            extend_subscription(sub, days=14)

    def test_extend_subscription_rejects_expired(self):
        sub = activate_subscription(self.user, self.plan_basic, source="manual", amount=0, provider="manual")
        sub.status = Subscription.Status.EXPIRED
        sub.save()
        with self.assertRaises(ValueError):
            extend_subscription(sub, days=14)

    def test_downgrade_clears_order_email_notification_settings(self):
        store = Store.objects.create(
            owner=self.user,
            name="Downgrade Store",
            code=allocate_unique_store_code("DOWNGRADE"),
            owner_name="O",
            owner_email=self.user.email,
        )
        StoreMembership.objects.create(
            user=self.user,
            store=store,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        )
        ss, _ = StoreSettings.objects.get_or_create(store=store)
        ss.email_notify_owner_on_order_received = True
        ss.email_customer_on_order_confirmed = True
        ss.save()

        activate_subscription(self.user, self.plan_premium, source="manual", amount=0, provider="manual")
        ss.refresh_from_db()
        self.assertFalse(ss.email_notify_owner_on_order_received)

        activate_subscription(self.user, self.plan_basic, source="manual", amount=0, provider="manual")
        ss.refresh_from_db()
        self.assertFalse(ss.email_notify_owner_on_order_received)
        self.assertFalse(ss.email_customer_on_order_confirmed)


class FeatureGateTests(TestCase):
    def setUp(self):
        self.plan_basic = Plan.objects.filter(is_default=True).first()
        if not self.plan_basic:
            self.plan_basic = Plan.objects.create(
                name="basic",
                price=0,
                billing_cycle="monthly",
                features=_plan_features(limits={"max_products": 100}),
                is_default=True,
                is_active=True,
            )
        self.plan_premium = Plan.objects.create(
            name="premium",
            price=999,
            billing_cycle="monthly",
            features=_plan_features(
                limits={"max_products": 500},
                features={"basic_analytics": True, "marketing_tools": True},
            ),
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="fguser",
            email="fg@example.com",
            password="pass",
            is_verified=True,
        )

    def test_no_subscription_returns_empty_features(self):
        self.assertFalse(has_feature(self.user, "basic_analytics"))
        self.assertEqual(get_limit(self.user, "max_products"), 0)

    def test_has_feature_returns_true_when_subscription_has_feature(self):
        activate_subscription(self.user, self.plan_premium, source="manual", amount=0, provider="manual")
        self.assertTrue(has_feature(self.user, "basic_analytics"))
        self.assertTrue(has_feature(self.user, "marketing_tools"))
        self.assertEqual(get_limit(self.user, "max_products"), 500)

    def test_get_limit_returns_zero_when_missing(self):
        config = get_feature_config(self.user)
        self.assertEqual(get_limit(self.user, "nonexistent_limit"), 0)

    def test_get_feature_config_returns_structure(self):
        config = get_feature_config(self.user)
        self.assertIn("features", config)
        self.assertIn("limits", config)
        self.assertIsInstance(config["features"], dict)
        self.assertIsInstance(config["limits"], dict)

    def test_require_feature_raises_when_not_allowed(self):
        from rest_framework.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            require_feature(self.user, "marketing_tools")

    def test_require_feature_passes_when_allowed(self):
        activate_subscription(self.user, self.plan_premium, source="manual", amount=0, provider="manual")
        require_feature(self.user, "marketing_tools")

    def test_expired_subscription_returns_empty_features(self):
        activate_subscription(self.user, self.plan_premium, source="manual", amount=0, provider="manual")
        sub = get_active_subscription(self.user)
        sub.status = Subscription.Status.EXPIRED
        sub.save()
        self.assertFalse(has_feature(self.user, "basic_analytics"))
        self.assertEqual(get_limit(self.user, "max_products"), 0)


class StoreCreationEnforcementTests(TestCase):
    """Verify store creation API enforces subscription and one store per owner."""

    def setUp(self):
        self.client = APIClient()
        self.plan_basic = Plan.objects.filter(is_default=True).first()
        if not self.plan_basic:
            self.plan_basic = Plan.objects.create(
                name="basic",
                price=0,
                billing_cycle="monthly",
                features=_plan_features(limits={"max_products": 100}),
                is_default=True,
                is_active=True,
            )
        self.plan_premium = Plan.objects.filter(name="premium").first()
        if not self.plan_premium:
            self.plan_premium = Plan.objects.create(
                name="premium",
                price=999,
                billing_cycle="monthly",
                features=_plan_features(
                    limits={"max_products": 500},
                    features={"basic_analytics": True, "marketing_tools": True},
                ),
                is_active=True,
            )
        self.user = User.objects.create_user(
            username="storeuser",
            email="s@example.com",
            password="pass",
            is_verified=True,
        )
        self.client.force_authenticate(user=self.user)

    def _create_store_via_api(self, **overrides):
        data = {
            "name": "My Store",
            "owner_first_name": "Owner",
            "owner_last_name": "Name",
            "owner_email": "owner@example.com",
        }
        data.update(overrides)
        return self.client.post(
            "/api/v1/store/",
            data,
            format="json",
            HTTP_HOST="localhost",
        )

    def test_store_creation_blocked_when_no_plan(self):
        Plan.objects.filter(is_default=True).update(is_default=False)
        try:
            resp = self._create_store_via_api()
            self.assertEqual(resp.status_code, 403)
            self.assertIn("No active subscription", resp.data.get("detail", ""))
        finally:
            Plan.objects.filter(name="basic").update(is_default=True)

    def test_store_creation_blocked_without_subscription_even_with_default_plan(self):
        resp = self._create_store_via_api()
        self.assertEqual(resp.status_code, 403)
        self.assertIn("No active subscription", resp.data.get("detail", ""))

    def test_store_creation_allowed_with_subscription(self):
        activate_subscription(self.user, self.plan_basic, source="manual", amount=0, provider="manual")
        resp = self._create_store_via_api()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Store.objects.filter(memberships__user=self.user, memberships__role="owner").count(), 1)
        self.assertNotIn("api_key", resp.data)
        store_pid = resp.data["public_id"]
        self.assertEqual(StoreApiKey.objects.filter(store__public_id=store_pid).count(), 0)

    def test_second_store_creation_blocked(self):
        activate_subscription(self.user, self.plan_basic, source="manual", amount=0, provider="manual")
        self._create_store_via_api()
        resp = self._create_store_via_api(name="Second Store", owner_email="o2@example.com")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already have a store", resp.data.get("detail", ""))


class InitiatePaymentApiTests(TestCase):
    """POST /api/v1/billing/payment/initiate/ — switch plan before paying."""

    def setUp(self):
        self.client = APIClient()
        self.plan_a = Plan.objects.create(
            name="plan_a",
            price=100,
            billing_cycle="monthly",
            features=_plan_features(),
            is_active=True,
        )
        self.plan_b = Plan.objects.create(
            name="plan_b",
            price=250,
            billing_cycle="monthly",
            features=_plan_features(),
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="payuser",
            email="pay@example.com",
            password="pass",
            is_verified=True,
        )
        self.client.force_authenticate(user=self.user)

    def test_initiate_twice_before_txn_updates_same_pending(self):
        r1 = self.client.post(
            "/api/v1/billing/payment/initiate/",
            {"plan_public_id": self.plan_a.public_id},
            format="json",
        )
        self.assertEqual(r1.status_code, 201)
        pay_id = r1.data["public_id"]
        self.assertEqual(Decimal(str(r1.data["amount"])), self.plan_a.price)

        r2 = self.client.post(
            "/api/v1/billing/payment/initiate/",
            {"plan_public_id": self.plan_b.public_id},
            format="json",
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["public_id"], pay_id)
        self.assertEqual(r2.data["plan"]["public_id"], self.plan_b.public_id)
        self.assertEqual(Decimal(str(r2.data["amount"])), self.plan_b.price)
        self.assertEqual(
            Payment.objects.filter(user=self.user, status=Payment.Status.PENDING).count(),
            1,
        )

    def test_initiate_blocked_after_transaction_submitted(self):
        self.client.post(
            "/api/v1/billing/payment/initiate/",
            {"plan_public_id": self.plan_a.public_id},
            format="json",
        )
        self.client.post(
            "/api/v1/billing/payment/submit/",
            {"transaction_id": "TXN-UNIQUE-001", "sender_number": ""},
            format="json",
        )
        r = self.client.post(
            "/api/v1/billing/payment/initiate/",
            {"plan_public_id": self.plan_b.public_id},
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("non_field_errors", r.data)


class FeaturesEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.plan_basic = Plan.objects.filter(is_default=True).first()
        if not self.plan_basic:
            self.plan_basic = Plan.objects.create(
                name="basic",
                price=0,
                billing_cycle="monthly",
                features=_plan_features(limits={"max_products": 100}),
                is_default=True,
                is_active=True,
            )
        self.user = User.objects.create_user(
            username="featuser",
            email="f@example.com",
            password="pass",
            is_verified=True,
        )
        self.client.force_authenticate(user=self.user)

    def test_features_endpoint_returns_empty_without_subscription(self):
        resp = self.client.get("/api/v1/auth/features/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("features", resp.data)
        self.assertIn("limits", resp.data)
        self.assertEqual(resp.data["features"], {})
        self.assertEqual(resp.data["limits"], {})

    def test_features_endpoint_returns_config_with_subscription(self):
        activate_subscription(self.user, self.plan_basic, source="manual", amount=0, provider="manual")
        resp = self.client.get("/api/v1/auth/features/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("features", resp.data)
        self.assertIn("limits", resp.data)
        self.assertIn("max_products", resp.data["limits"])


class MeSubscriptionPayloadTests(TestCase):
    """Verify /auth/me subscription payload includes expiration fields."""

    def setUp(self):
        self.client = APIClient()
        self.plan = Plan.objects.filter(is_default=True).first()
        if not self.plan:
            self.plan = Plan.objects.create(
                name="basic",
                price=0,
                billing_cycle="monthly",
                features=_plan_features(limits={"max_products": 100}),
                is_default=True,
                is_active=True,
            )
        self.user = User.objects.create_user(
            username="meuser",
            email="me@example.com",
            password="pass",
            is_verified=True,
        )
        self.client.force_authenticate(user=self.user)

    def test_no_subscription_returns_inactive_with_zero_days(self):
        resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(resp.status_code, 200)
        sub = resp.data["subscription"]
        self.assertFalse(sub["active"])
        self.assertEqual(sub["days_remaining"], 0)
        self.assertFalse(sub["is_expiring_soon"])
        self.assertIsNone(sub["plan"])
        self.assertIsNone(sub["end_date"])

    def test_active_subscription_returns_days_remaining(self):
        activate_subscription(
            self.user, self.plan, source="manual", amount=0,
            provider="manual", duration_days=30,
        )
        resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(resp.status_code, 200)
        sub = resp.data["subscription"]
        self.assertTrue(sub["active"])
        self.assertEqual(sub["days_remaining"], 30)
        self.assertFalse(sub["is_expiring_soon"])
        self.assertIsNotNone(sub["end_date"])

    def test_expiring_soon_subscription_flagged(self):
        activate_subscription(
            self.user, self.plan, source="manual", amount=0,
            provider="manual", duration_days=2,
        )
        resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(resp.status_code, 200)
        sub = resp.data["subscription"]
        self.assertTrue(sub["active"])
        self.assertEqual(sub["days_remaining"], 2)
        self.assertTrue(sub["is_expiring_soon"])
