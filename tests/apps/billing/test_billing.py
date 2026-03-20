"""Billing app tests."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from engine.apps.billing.feature_gate import (
    get_feature_config,
    get_limit,
    has_feature,
    require_feature,
)
from engine.apps.billing.models import Plan, Subscription
from engine.apps.billing.services import activate_subscription, extend_subscription, get_active_subscription
from engine.apps.stores.models import Store, StoreMembership

User = get_user_model()


def _plan_features(limits=None, features=None):
    return {
        "limits": limits or {"max_stores": 1},
        "features": features or {"advanced_analytics": False, "marketing_tools": False},
    }


class BillingServicesTests(TestCase):
    def setUp(self):
        self.plan_basic = Plan.objects.filter(is_default=True).first()
        if not self.plan_basic:
            self.plan_basic = Plan.objects.create(
                name="basic",
                price=0,
                billing_cycle="monthly",
                features=_plan_features(limits={"max_stores": 1}),
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
                    limits={"max_stores": 3},
                    features={"advanced_analytics": True, "marketing_tools": True},
                ),
                is_active=True,
            )
        self.user = User.objects.create_user(username="billinguser", email="b@example.com", password="pass")

    def test_get_active_subscription_returns_none_when_no_subscription(self):
        self.assertIsNone(get_active_subscription(self.user))

    def test_activate_subscription_creates_subscription_and_payment(self):
        sub = activate_subscription(
            user=self.user,
            plan=self.plan_basic,
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


class FeatureGateTests(TestCase):
    def setUp(self):
        self.plan_basic = Plan.objects.filter(is_default=True).first()
        if not self.plan_basic:
            self.plan_basic = Plan.objects.create(
                name="basic",
                price=0,
                billing_cycle="monthly",
                features=_plan_features(limits={"max_stores": 1}),
                is_default=True,
                is_active=True,
            )
        self.plan_premium = Plan.objects.create(
            name="premium",
            price=999,
            billing_cycle="monthly",
            features=_plan_features(
                limits={"max_stores": 3},
                features={"advanced_analytics": True, "marketing_tools": True},
            ),
            is_active=True,
        )
        self.user = User.objects.create_user(username="fguser", email="fg@example.com", password="pass")

    def test_has_feature_returns_false_without_subscription_uses_default(self):
        self.assertFalse(has_feature(self.user, "advanced_analytics"))
        self.assertEqual(get_limit(self.user, "max_stores"), 1)

    def test_has_feature_returns_true_when_subscription_has_feature(self):
        activate_subscription(self.user, self.plan_premium, source="manual", amount=0, provider="manual")
        self.assertTrue(has_feature(self.user, "advanced_analytics"))
        self.assertTrue(has_feature(self.user, "marketing_tools"))
        self.assertEqual(get_limit(self.user, "max_stores"), 3)

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
            require_feature(self.user, "advanced_analytics")

    def test_require_feature_passes_when_allowed(self):
        activate_subscription(self.user, self.plan_premium, source="manual", amount=0, provider="manual")
        require_feature(self.user, "advanced_analytics")

    def test_expired_subscription_uses_default_plan(self):
        activate_subscription(self.user, self.plan_premium, source="manual", amount=0, provider="manual")
        sub = get_active_subscription(self.user)
        sub.status = Subscription.Status.EXPIRED
        sub.save()
        self.assertFalse(has_feature(self.user, "advanced_analytics"))
        self.assertEqual(get_limit(self.user, "max_stores"), 1)


class StoreCreationEnforcementTests(TestCase):
    """Verify store creation API enforces subscription and plan limits."""

    def setUp(self):
        self.client = APIClient()
        self.plan_basic = Plan.objects.filter(is_default=True).first()
        if not self.plan_basic:
            self.plan_basic = Plan.objects.create(
                name="basic",
                price=0,
                billing_cycle="monthly",
                features=_plan_features(limits={"max_stores": 1}),
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
                    limits={"max_stores": 3},
                    features={"advanced_analytics": True, "marketing_tools": True},
                ),
                is_active=True,
            )
        self.user = User.objects.create_user(username="storeuser", email="s@example.com", password="pass")
        self.client.force_authenticate(user=self.user)

    def _create_store_via_api(self, **overrides):
        data = {
            "name": "My Store",
            "owner_name": "Owner",
            "owner_email": "owner@example.com",
        }
        data.update(overrides)
        return self.client.post(
            "/api/v1/stores/",
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

    def test_store_creation_allowed_with_default_plan_no_subscription(self):
        resp = self._create_store_via_api()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Store.objects.filter(memberships__user=self.user, memberships__role="owner").count(), 1)

    def test_store_creation_allowed_with_subscription(self):
        activate_subscription(self.user, self.plan_basic, source="manual", amount=0, provider="manual")
        resp = self._create_store_via_api()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(Store.objects.filter(memberships__user=self.user, memberships__role="owner").count(), 1)

    def test_store_creation_blocked_when_limit_reached(self):
        activate_subscription(self.user, self.plan_basic, source="manual", amount=0, provider="manual")
        self._create_store_via_api()
        resp = self._create_store_via_api(name="Second Store", owner_email="o2@example.com")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Store limit reached", resp.data.get("detail", ""))


class FeaturesEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.plan_basic = Plan.objects.filter(is_default=True).first()
        if not self.plan_basic:
            self.plan_basic = Plan.objects.create(
                name="basic",
                price=0,
                billing_cycle="monthly",
                features=_plan_features(limits={"max_stores": 1}),
                is_default=True,
                is_active=True,
            )
        self.user = User.objects.create_user(username="featuser", email="f@example.com", password="pass")
        self.client.force_authenticate(user=self.user)

    def test_features_endpoint_returns_config(self):
        resp = self.client.get("/api/v1/auth/features/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("features", resp.data)
        self.assertIn("limits", resp.data)
        self.assertIn("max_stores", resp.data["limits"])


class AnalyticsFeatureGateTests(TestCase):
    """Verify analytics endpoint requires advanced_analytics feature."""

    def setUp(self):
        self.client = APIClient()
        self.store = Store.objects.create(
            name="Test Store",
            domain="analytics-test.local",
            owner_name="Owner",
            owner_email="owner@example.com",
        )
        self.plan_basic = Plan.objects.filter(is_default=True).first()
        if not self.plan_basic:
            self.plan_basic = Plan.objects.create(
                name="basic",
                price=0,
                billing_cycle="monthly",
                features=_plan_features(limits={"max_stores": 1}),
                is_default=True,
                is_active=True,
            )
        self.plan_premium = Plan.objects.create(
            name="premium",
            price=999,
            billing_cycle="monthly",
            features=_plan_features(
                limits={"max_stores": 3},
                features={"advanced_analytics": True, "marketing_tools": True},
            ),
            is_active=True,
        )
        self.user = User.objects.create_user(username="analyticsuser", email="a@example.com", password="pass")
        StoreMembership.objects.create(user=self.user, store=self.store, role=StoreMembership.Role.OWNER)
        self.client.force_authenticate(user=self.user)

    def test_analytics_blocked_without_advanced_analytics(self):
        activate_subscription(self.user, self.plan_basic, source="manual", amount=0, provider="manual")
        resp = self.client.get(
            "/api/v1/admin/analytics/overview/",
            HTTP_HOST="analytics-test.local",
            HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(resp.status_code, 403)

    def test_analytics_allowed_with_advanced_analytics(self):
        activate_subscription(self.user, self.plan_premium, source="manual", amount=0, provider="manual")
        resp = self.client.get(
            "/api/v1/admin/analytics/overview/",
            HTTP_HOST="analytics-test.local",
            HTTP_X_STORE_ID=str(self.store.id),
        )
        self.assertEqual(resp.status_code, 200)
