"""Day-two rate limiting: fail-open cache, plan-tier aggregate limits."""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase, override_settings

from engine.apps.billing.feature_gate import invalidate_feature_config_cache
from engine.apps.billing.models import Plan
from engine.apps.billing.services import activate_subscription
from engine.apps.stores.models import Store, StoreMembership
from engine.apps.stores.services import allocate_unique_store_code
from engine.core.redis_fixed_window import incr_under_limit
from engine.core.rate_limit import resolve_storefront_aggregate_limit, storefront_rate_check

User = get_user_model()


def test_incr_under_limit_fail_open_on_cache_error():
    cache = caches["default"]

    def boom(*args, **kwargs):
        raise RuntimeError("redis down")

    with patch("engine.core.redis_fixed_window.fixed_window_increment", side_effect=boom):
        assert incr_under_limit(cache, "rate:test:failopen", 60, 10) is True


@pytest.mark.django_db
def test_resolve_storefront_aggregate_limit_uses_plan_limits():
    plan = Plan.objects.create(
        name="rpm-test-plan",
        price=0,
        billing_cycle=Plan.BillingCycle.MONTHLY,
        features={
            "limits": {"max_stores": 1, "storefront_aggregate_rpm": 777},
            "features": {},
        },
        is_active=True,
    )
    user = User.objects.create_user(
        email="owner-rpm@example.com",
        password="pass12345",
        is_verified=True,
    )
    activate_subscription(
        user=user,
        plan=plan,
        billing_cycle="monthly",
        duration_days=30,
        source="manual",
        amount=0,
        provider="manual",
    )
    store = Store.objects.create(
        name="RPM Store",
        code=allocate_unique_store_code("RPM"),
        owner_name="O",
        owner_email=user.email,
    )
    StoreMembership.objects.create(
        user=user,
        store=store,
        role=StoreMembership.Role.OWNER,
        is_active=True,
    )
    invalidate_feature_config_cache(user)

    assert resolve_storefront_aggregate_limit(store) == 777


@pytest.mark.django_db
def test_resolve_storefront_aggregate_limit_fallback_without_owner():
    store = Store.objects.create(
        name="No Owner Store",
        code=allocate_unique_store_code("NOOWN"),
        owner_name="Ghost",
        owner_email="ghost@example.com",
    )
    with override_settings(TENANT_API_KEY_AGGREGATE_RATE_LIMIT_PER_MIN=4242):
        assert resolve_storefront_aggregate_limit(store) == 4242


@pytest.mark.django_db
def test_storefront_rate_check_returns_aggregate_reason():
    plan = Plan.objects.create(
        name="tight-rpm-plan",
        price=0,
        billing_cycle=Plan.BillingCycle.MONTHLY,
        features={
            "limits": {"max_stores": 1, "storefront_aggregate_rpm": 1},
            "features": {},
        },
        is_active=True,
    )
    user = User.objects.create_user(
        email="tight@example.com",
        password="pass12345",
        is_verified=True,
    )
    activate_subscription(
        user=user,
        plan=plan,
        billing_cycle="monthly",
        duration_days=30,
        source="manual",
        amount=0,
        provider="manual",
    )
    store = Store.objects.create(
        name="Tight Store",
        code=allocate_unique_store_code("TIGHT"),
        owner_name="O",
        owner_email=user.email,
    )
    StoreMembership.objects.create(
        user=user,
        store=store,
        role=StoreMembership.Role.OWNER,
        is_active=True,
    )
    invalidate_feature_config_cache(user)

    sid = str(store.public_id)
    ok1, r1 = storefront_rate_check(
        store=store,
        store_public_id=sid,
        api_key_public_id="key_test_1",
        client_ip="192.0.2.1",
    )
    ok2, r2 = storefront_rate_check(
        store=store,
        store_public_id=sid,
        api_key_public_id="key_test_1",
        client_ip="192.0.2.1",
    )
    assert ok1 is True and r1 is None
    assert ok2 is False and r2 == "aggregate"


class RateLimitMiddlewareLoggingTests(TestCase):
    """Ensure 429 path logs a warning (observer)."""

    def test_middleware_logs_warning_on_429(self):
        from django.test import RequestFactory

        from engine.apps.stores.services import create_store_api_key
        from engine.core.middleware.internal_override_middleware import InternalOverrideMiddleware
        from engine.core.rate_limit import ApiKeyRateLimitMiddleware
        from engine.core.store_api_key_auth import TenantApiKeyMiddleware

        plan = Plan.objects.create(
            name="log-rpm-plan",
            price=0,
            billing_cycle=Plan.BillingCycle.MONTHLY,
            features={
                "limits": {"max_stores": 1, "storefront_aggregate_rpm": 1},
                "features": {},
            },
            is_active=True,
        )
        user = User.objects.create_user(
            email="logmw@example.com",
            password="pass12345",
            is_verified=True,
        )
        activate_subscription(
            user=user,
            plan=plan,
            billing_cycle="monthly",
            duration_days=30,
            source="manual",
            amount=0,
            provider="manual",
        )
        store = Store.objects.create(
            name="Log MW Store",
            code=allocate_unique_store_code("LOGMW"),
            owner_name="O",
            owner_email=user.email,
        )
        StoreMembership.objects.create(
            user=user,
            store=store,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        )
        invalidate_feature_config_cache(user)
        _row, raw_key = create_store_api_key(store, name="fe")

        factory = RequestFactory()
        path = "/api/v1/products/"

        def chain(req):
            InternalOverrideMiddleware(lambda r: None).process_request(req)
            TenantApiKeyMiddleware(lambda r: None).process_request(req)
            return ApiKeyRateLimitMiddleware(lambda r: None).process_request(req)

        with self.assertLogs("engine.core.rate_limit", level="WARNING") as cm:
            req1 = factory.get(path, HTTP_AUTHORIZATION=f"Bearer {raw_key}")
            req1.user = user
            self.assertIsNone(chain(req1))
            req2 = factory.get(path, HTTP_AUTHORIZATION=f"Bearer {raw_key}")
            req2.user = user
            resp = chain(req2)
            self.assertIsNotNone(resp)
            self.assertEqual(resp.status_code, 429)

        self.assertTrue(
            any("storefront rate limit exceeded" in line for line in cm.output),
            cm.output,
        )
        self.assertEqual(cm.records[-1].store_public_id, str(store.public_id))
        self.assertEqual(cm.records[-1].rate_limit_reason, "aggregate")
