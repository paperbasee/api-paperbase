"""Unit tests for ``tiktok_enqueue_payload``."""

from __future__ import annotations

import decimal
from unittest import TestCase

from engine.apps.tracking.tiktok_payload import PII_FIELDS, tiktok_enqueue_payload


class TikTokEnqueuePayloadTests(TestCase):
    def _base_validated(self) -> dict:
        return {
            "event_id": "e1",
            "event_name": "Purchase",
            "event_time": 1700000001,
            "event_source_url": "https://shop.example.com/thanks",
            "user_agent": "Mozilla/5.0",
            "value": 1.0,
            "currency": "USD",
            "content_type": "product",
            "content_ids": [],
        }

    def test_includes_ttp_when_present(self):
        data = self._base_validated()
        data["ttp"] = "cookie-ttp"
        out = tiktok_enqueue_payload(data, client_ip="1.1.1.1")
        self.assertEqual(out.get("ttp"), "cookie-ttp")

    def test_includes_ttclid_when_present(self):
        data = self._base_validated()
        data["ttclid"] = "click-id-xyz"
        out = tiktok_enqueue_payload(data, client_ip=None)
        self.assertEqual(out.get("ttclid"), "click-id-xyz")

    def test_missing_ttp_ttclid_are_none_or_absent_ok(self):
        out = tiktok_enqueue_payload(self._base_validated(), client_ip="")
        self.assertIsNone(out.get("ttp"))
        self.assertIsNone(out.get("ttclid"))

    def test_empty_ttp_ttclid_forwarded(self):
        data = self._base_validated()
        data["ttp"] = ""
        data["ttclid"] = ""
        out = tiktok_enqueue_payload(data, client_ip="")
        self.assertEqual(out.get("ttp"), "")
        self.assertEqual(out.get("ttclid"), "")

    def test_forwards_pii_fields_for_server_side_hashing(self):
        data = self._base_validated()
        for f in PII_FIELDS:
            data[f] = f"val-{f}"
        out = tiktok_enqueue_payload(data, client_ip="9.9.9.9")
        for f in PII_FIELDS:
            with self.subTest(field=f):
                self.assertEqual(out.get(f), f"val-{f}")

    def test_does_not_include_fbp_or_fbc(self):
        data = self._base_validated()
        data["fbp"] = "fbp-token"
        data["fbc"] = "fbc-token"
        out = tiktok_enqueue_payload(data, client_ip="")
        self.assertNotIn("fbp", out)
        self.assertNotIn("fbc", out)

    def test_decimal_value_does_not_raise(self):
        data = self._base_validated()
        data["value"] = decimal.Decimal("19.99")
        out = tiktok_enqueue_payload(data, client_ip="")
        self.assertEqual(out["value"], 19.99)
