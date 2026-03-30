"""Admin API: product count caps from plan.features.limits.max_products."""

import uuid
from decimal import Decimal

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from engine.apps.billing.models import Plan
from engine.apps.billing.services import activate_subscription
from engine.apps.products.models import Product
from engine.apps.stores.models import StoreMembership
from engine.core.ids import generate_public_id
from engine.core.tenant_execution import tenant_scope_from_store
from tests.core.test_core import _make_category, _make_store, make_user

LIMIT_DETAIL = "You have reached your product limit for your current plan."


def _bulk_products(store, category, n: int) -> None:
    with tenant_scope_from_store(store=store, reason="test"):
        Product.objects.bulk_create(
            [
                Product(
                    id=uuid.uuid4(),
                    store=store,
                    category=category,
                    name=f"seed{i}",
                    slug=f"seed-{store.id}-{i}",
                    public_id=generate_public_id("product"),
                    price=Decimal("1.00"),
                    status=Product.Status.ACTIVE,
                    is_active=True,
                )
                for i in range(n)
            ]
        )


class ProductPlanLimitTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = _make_store("Plan Limit Store", "plan-limit.local")
        self.user = make_user("plan-limit-owner@example.com")
        StoreMembership.objects.create(
            user=self.user,
            store=self.store,
            role=StoreMembership.Role.OWNER,
            is_active=True,
        )
        self.client.force_authenticate(user=self.user)
        self.category = _make_category(self.store, "PlanLimitCat")

    def _headers(self):
        return {"HTTP_X_STORE_PUBLIC_ID": self.store.public_id}

    def _post_product(self, name: str = "API Product"):
        return self.client.post(
            "/api/v1/admin/products/",
            {
                "name": name,
                "price": "9.99",
                "category": self.category.public_id,
                "is_active": True,
                "description": "",
            },
            format="json",
            **self._headers(),
        )

    def test_max_products_100_allows_100_blocks_101(self):
        plan = Plan.objects.create(
            name=f"tier-100-{uuid.uuid4().hex[:10]}",
            price=Decimal("0.00"),
            billing_cycle=Plan.BillingCycle.MONTHLY,
            is_active=True,
            features={
                "limits": {"max_stores": 5, "max_products": 100},
                "features": {},
            },
        )
        activate_subscription(
            self.user,
            plan,
            source="manual",
            amount=0,
            provider="manual",
        )
        _bulk_products(self.store, self.category, 99)
        r1 = self._post_product("Hundredth")
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED, r1.data)
        r2 = self._post_product("HundredFirst")
        self.assertEqual(r2.status_code, status.HTTP_403_FORBIDDEN, r2.data)
        self.assertEqual(r2.data.get("detail"), LIMIT_DETAIL)

    def test_max_products_200_allows_200_blocks_201(self):
        plan = Plan.objects.create(
            name=f"tier-200-{uuid.uuid4().hex[:10]}",
            price=Decimal("0.00"),
            billing_cycle=Plan.BillingCycle.MONTHLY,
            is_active=True,
            features={
                "limits": {"max_stores": 5, "max_products": 200},
                "features": {},
            },
        )
        activate_subscription(
            self.user,
            plan,
            source="manual",
            amount=0,
            provider="manual",
        )
        _bulk_products(self.store, self.category, 199)
        r1 = self._post_product("TwoHundredth")
        self.assertEqual(r1.status_code, status.HTTP_201_CREATED, r1.data)
        r2 = self._post_product("TwoHundredFirst")
        self.assertEqual(r2.status_code, status.HTTP_403_FORBIDDEN, r2.data)
        self.assertEqual(r2.data.get("detail"), LIMIT_DETAIL)

    def test_missing_max_products_allows_create(self):
        plan = Plan.objects.create(
            name=f"no-prod-cap-{uuid.uuid4().hex[:10]}",
            price=Decimal("0.00"),
            billing_cycle=Plan.BillingCycle.MONTHLY,
            is_active=True,
            features={
                "limits": {"max_stores": 1},
                "features": {},
            },
        )
        activate_subscription(
            self.user,
            plan,
            source="manual",
            amount=0,
            provider="manual",
        )
        _bulk_products(self.store, self.category, 15)
        r = self._post_product("OverSoftLimit")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
