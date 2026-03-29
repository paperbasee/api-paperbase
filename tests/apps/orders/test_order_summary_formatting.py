"""Tests for structured order text used in transactional emails."""

import uuid as _uuid
from decimal import Decimal

from django.test import TestCase

from engine.apps.orders.models import Order
from engine.apps.orders.order_summary_formatting import (
    DISTRICT_NOT_SPECIFIED,
    build_order_email_context,
    build_structured_order_summary,
    format_item_lines,
    resolve_district,
)
from engine.apps.products.models import Category, Product
from engine.apps.shipping.models import ShippingZone
from engine.core.tenant_execution import tenant_scope_from_store


def _store():
    d = f"t{_uuid.uuid4().hex[:12]}.local"
    from engine.apps.stores.models import Store

    return Store.objects.create(
        name="S",
        owner_name="O",
        owner_email=f"owner@{d}",
        currency="BDT",
        currency_symbol="৳",
    )


def _zone(store):
    return ShippingZone.objects.create(
        store=store,
        name=f"Z{_uuid.uuid4().hex[:6]}",
        is_active=True,
    )


def _product(store):
    with tenant_scope_from_store(store=store, reason="test fixture"):
        cat = Category.objects.create(
            store=store,
            name=f"C {_uuid.uuid4().hex[:8]}",
            slug="",
        )
        return Product.objects.create(
            store=store,
            category=cat,
            name="Widget",
            slug=f"w-{_uuid.uuid4().hex[:8]}",
            price=Decimal("65.00"),
        )


def _order(store, **kwargs):
    order_number = f"T{_uuid.uuid4().hex[:8].upper()}"
    defaults = dict(
        store=store,
        order_number=order_number,
        email="c@example.com",
        shipping_name="Jane Doe",
        shipping_address="12 Road, Dhaka",
        phone="01711111111",
        district="",
        shipping_zone=_zone(store),
        total=Decimal("190.00"),
        shipping_cost=Decimal("60.00"),
    )
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


class ResolveDistrictTests(TestCase):
    def test_uses_order_district_when_set(self):
        store = _store()
        o = _order(store, district="Chittagong", shipping_address="Somewhere")
        self.assertEqual(resolve_district(o), "Chittagong")

    def test_falls_back_to_last_comma_segment(self):
        store = _store()
        o = _order(store, district="", shipping_address="House 1, Dhanmondi, Dhaka")
        self.assertEqual(resolve_district(o), "Dhaka")

    def test_not_specified_when_no_clues(self):
        store = _store()
        o = _order(store, district="", shipping_address="SingleLineNoComma")
        self.assertEqual(resolve_district(o), DISTRICT_NOT_SPECIFIED)


class BuildStructuredSummaryTests(TestCase):
    def test_includes_delivery_total_items_district(self):
        store = _store()
        o = _order(
            store,
            district="Dhaka",
            shipping_address="12 Road",
        )
        from engine.apps.orders.models import OrderItem

        p = _product(store)
        OrderItem.objects.create(
            order=o,
            product=p,
            quantity=2,
            unit_price=Decimal("65.00"),
            original_price=Decimal("65.00"),
            discount_amount=Decimal("0.00"),
            line_subtotal=Decimal("130.00"),
            line_total=Decimal("130.00"),
        )
        text = build_structured_order_summary(o)
        self.assertIn(f"Order: #{o.order_number}", text)
        self.assertIn("Customer: Jane Doe", text)
        self.assertIn("Phone: 01711111111", text)
        self.assertIn("Address: 12 Road", text)
        self.assertIn("District: Dhaka", text)
        self.assertIn("- Widget x2", text)
        self.assertIn("Delivery charge: 60.00", text)
        self.assertIn("Total: 190.00", text)
        self.assertIn("BDT", text)

    def test_does_not_mutate_order_amounts(self):
        store = _store()
        o = _order(store, total=Decimal("10.00"), shipping_cost=Decimal("2.00"))
        t0, s0 = o.total, o.shipping_cost
        build_structured_order_summary(o)
        o.refresh_from_db()
        self.assertEqual(o.total, t0)
        self.assertEqual(o.shipping_cost, s0)


class BuildOrderEmailContextTests(TestCase):
    def test_has_order_summary_and_discrete_keys(self):
        store = _store()
        o = _order(store, district="Dhaka")
        ctx = build_order_email_context(o)
        self.assertIn("order_summary", ctx)
        self.assertEqual(ctx["district"], "Dhaka")
        self.assertEqual(ctx["delivery_charge"], "60.00")
        self.assertEqual(ctx["total"], "190.00")
        self.assertEqual(ctx["currency"], "BDT")


class FormatItemLinesTests(TestCase):
    def test_empty_items(self):
        store = _store()
        o = _order(store)
        self.assertEqual(format_item_lines(o), [])
