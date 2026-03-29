"""Tests for Steadfast (Packzy) minimal create_order payload."""

import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from engine.apps.couriers.models import Courier
from engine.apps.couriers.services.steadfast_service import (
    _extract_consignment_id,
    build_create_order_payload,
    create_order,
    normalize_phone_number,
)
from engine.apps.orders.models import Order
from engine.apps.shipping.models import ShippingZone
from engine.core.encryption import encrypt_value


def _store():
    d = f"t{uuid.uuid4().hex[:12]}.local"
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
        name=f"Z{uuid.uuid4().hex[:6]}",
        is_active=True,
    )


def _order(store, **kwargs):
    order_number = f"T{uuid.uuid4().hex[:8].upper()}"
    defaults = dict(
        store=store,
        order_number=order_number,
        email="c@example.com",
        shipping_name="Jane Doe",
        shipping_address="12 Road, Dhanmondi",
        phone="01711111111",
        district="Dhaka",
        shipping_zone=_zone(store),
        total=Decimal("190.00"),
        shipping_cost=Decimal("60.00"),
    )
    defaults.update(kwargs)
    return Order.objects.create(**defaults)


class BuildCreateOrderPayloadTests(TestCase):
    def test_legacy_five_core_keys_only(self):
        store = _store()
        o = _order(store)
        payload = build_create_order_payload(o)
        self.assertEqual(
            set(payload.keys()),
            {
                "invoice",
                "recipient_name",
                "recipient_phone",
                "recipient_address",
                "cod_amount",
            },
        )
        self.assertNotIn("note", payload)
        self.assertIsInstance(payload["cod_amount"], float)
        self.assertEqual(payload["cod_amount"], 190.0)
        self.assertEqual(payload["invoice"], o.order_number)
        self.assertNotIn("email", payload)

    def test_recipient_address_three_part_unchanged_when_last_matches_district(self):
        store = _store()
        o = _order(
            store,
            shipping_address="House 1, Atrai, Naogaon",
            district="Naogaon",
        )
        payload = build_create_order_payload(o)
        self.assertEqual(payload["recipient_address"], "House 1, Atrai, Naogaon")

    def test_appends_district_when_not_already_in_address(self):
        store = _store()
        o = _order(
            store,
            shipping_address="Town School Playground, Bhola Sadar",
            district="Bhola",
        )
        payload = build_create_order_payload(o)
        self.assertEqual(
            payload["recipient_address"],
            "Town School Playground, Bhola Sadar, Bhola",
        )

    def test_recipient_address_only_district_when_shipping_empty(self):
        store = _store()
        o = _order(store, shipping_address="", district="Bhola")
        payload = build_create_order_payload(o)
        self.assertEqual(payload["recipient_address"], "Bhola")

    def test_normalizes_phone_with_country_code(self):
        store = _store()
        o = _order(store, phone="+8801712345678")
        payload = build_create_order_payload(o)
        self.assertEqual(payload["recipient_phone"], "01712345678")

    def test_raises_on_invalid_phone(self):
        store = _store()
        o = _order(store, phone="123")
        with self.assertRaises(ValidationError):
            build_create_order_payload(o)

    def test_truncates_recipient_name_to_100_chars(self):
        store = _store()
        long_name = "N" * 120
        o = _order(store, shipping_name=long_name)
        payload = build_create_order_payload(o)
        self.assertEqual(len(payload["recipient_name"]), 100)
        self.assertEqual(payload["recipient_name"], long_name[:100])

    def test_truncates_recipient_address_to_250_chars(self):
        store = _store()
        long_addr = "X" * 300
        o = _order(store, shipping_address=long_addr)
        payload = build_create_order_payload(o)
        self.assertEqual(len(payload["recipient_address"]), 250)


class NormalizePhoneNumberTests(TestCase):
    def test_strips_non_digits_and_880_prefix(self):
        self.assertEqual(normalize_phone_number("+8801712345678"), "01712345678")
        self.assertEqual(normalize_phone_number("8801712345678"), "01712345678")

    def test_preserves_11_digit_local(self):
        self.assertEqual(normalize_phone_number("01711111111"), "01711111111")


class ExtractConsignmentIdTests(TestCase):
    def test_data_wrapper_with_consignment_id_string(self):
        self.assertEqual(
            _extract_consignment_id({"data": {"consignment_id": "C-1"}}),
            "C-1",
        )

    def test_data_wrapper_numeric_consignment_id(self):
        self.assertEqual(
            _extract_consignment_id({"data": {"consignment_id": 1424107}}),
            "1424107",
        )

    def test_top_level_consignment_id(self):
        self.assertEqual(
            _extract_consignment_id({"consignment_id": "X-9"}),
            "X-9",
        )

    def test_nested_consignment_object(self):
        self.assertEqual(
            _extract_consignment_id(
                {
                    "consignment": {
                        "consignment_id": 99,
                        "tracking_code": "TRK",
                    }
                }
            ),
            "99",
        )

    def test_data_nested_consignment_prefers_consignment_id_over_tracking(self):
        self.assertEqual(
            _extract_consignment_id(
                {
                    "data": {
                        "consignment": {
                            "consignment_id": 1,
                            "tracking_code": "ABC",
                        }
                    }
                }
            ),
            "1",
        )

    def test_nested_tracking_code_when_no_consignment_id(self):
        self.assertEqual(
            _extract_consignment_id(
                {"data": {"consignment": {"tracking_code": "15BAEB8A"}}}
            ),
            "15BAEB8A",
        )

    def test_empty_when_missing(self):
        self.assertEqual(_extract_consignment_id({"data": {"message": "ok"}}), "")
        self.assertEqual(_extract_consignment_id({}), "")


class CreateOrderTests(TestCase):
    def setUp(self):
        self.store = _store()
        self.order = _order(self.store)
        self.courier = Courier.objects.create(
            store=self.store,
            provider=Courier.Provider.STEADFAST,
            api_key_encrypted=encrypt_value("api"),
            secret_key_encrypted=encrypt_value("secret"),
            is_active=True,
        )

    @patch("engine.apps.couriers.services.steadfast_service.requests.post")
    def test_calls_api_with_minimal_address_no_district_validation(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"consignment_id": "C-1"}}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        minimal = _order(self.store, district="", shipping_address="nocommas")
        out = create_order(minimal, self.courier)
        self.assertEqual(out["consignment_id"], "C-1")
        mock_post.assert_called_once()
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["recipient_address"], "nocommas")

    @patch("engine.apps.couriers.services.steadfast_service.requests.post")
    def test_posts_json_payload_only_five_core_fields(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"consignment_id": "C-1"}}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        out = create_order(self.order, self.courier)
        self.assertEqual(out["consignment_id"], "C-1")
        mock_post.assert_called_once()
        _args, kwargs = mock_post.call_args
        body = kwargs["json"]
        self.assertEqual(len(body), 5)
        self.assertNotIn("note", body)

    @patch("engine.apps.couriers.services.steadfast_service.requests.post")
    def test_parses_nested_data_consignment_object(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": 200,
            "data": {
                "consignment": {
                    "consignment_id": 1424107,
                    "invoice": "INV-1",
                },
            },
        }
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        out = create_order(self.order, self.courier)
        self.assertEqual(out["consignment_id"], "1424107")
