"""Tracking ingest: JSON-only contract and no form-field explosion."""

import json
from urllib.parse import urlencode

from django.test import Client, TestCase
from unittest.mock import patch

from engine.apps.tracking.capi_payload import capi_enqueue_payload
from engine.apps.tracking.tiktok_payload import tiktok_enqueue_payload
from tests.apps.stores.test_api_keys import create_store_api_key, make_store


class TrackingEventIngestContentTypeTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_urlencoded_many_fields_returns_415_not_500(self):
        """
        Huge application/x-www-form-urlencoded bodies used to trip
        DATA_UPLOAD_MAX_NUMBER_FIELDS (TooManyFieldsSent). Reject non-JSON
        before Django parses the form.
        """
        fields = {f"f{i}": "x" for i in range(2500)}
        body = urlencode(fields).encode("utf-8")
        response = self.client.post(
            "/tracking/event",
            data=body,
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 415)
        self.assertIn(b"application/json", response.content.lower())

    def test_json_without_auth_returns_401(self):
        response = self.client.post(
            "/tracking/event",
            data=b"{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class TrackingEventIngestDualBufferTests(TestCase):
    """Ingest pushes Meta and TikTok broker payloads; TikTok failures are non-fatal."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.store = make_store("Tracking Dual Buffer Store")
        _row, self.api_key = create_store_api_key(self.store, name="tracking-dual-buffer")

    def _valid_body(self, event_id_suffix: str) -> dict:
        return {
            "event_name": "PageView",
            "event_id": f"evt-ingest-{event_id_suffix}",
            "event_time": 1700000000,
            "event_source_url": "https://example.com/product",
            "user_agent": "Mozilla/5.0 (TrackingTest)",
            "value": 0.0,
            "currency": "USD",
            "fbp": "fb.1.meta",
            "fbc": "fb.2.meta",
            "ttp": "ttp-from-cookie",
            "ttclid": "ttclid-from-url",
        }

    @patch("engine.apps.tracking.views.cache.add", return_value=True)
    @patch("engine.apps.tracking.buffer.push_event_to_buffer", return_value=True)
    def test_successful_ingest_pushes_meta_then_tiktok_same_store_id(self, mock_push, _cache_add):
        body = self._valid_body("dual-1")
        resp = self.client.post(
            "/tracking/event",
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_push.call_count, 2)
        self.assertEqual(mock_push.call_args_list[0].kwargs.get("platform"), "meta")
        self.assertEqual(mock_push.call_args_list[1].kwargs.get("platform"), "tiktok")
        store_id = str(self.store.public_id)
        self.assertEqual(mock_push.call_args_list[0].args[0], store_id)
        self.assertEqual(mock_push.call_args_list[1].args[0], store_id)

    @patch("engine.apps.tracking.views.cache.add", return_value=True)
    @patch("engine.apps.tracking.buffer.push_event_to_buffer", return_value=False)
    def test_meta_buffer_push_failure_returns_503(self, mock_push, _cache_add):
        resp = self.client.post(
            "/tracking/event",
            data=json.dumps(self._valid_body("meta-fail")),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key}",
        )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(mock_push.call_count, 1)
        self.assertEqual(mock_push.call_args_list[0].kwargs.get("platform"), "meta")

    @patch("engine.apps.tracking.views.cache.add", return_value=True)
    def test_tiktok_buffer_push_failure_still_returns_200(self, _cache_add):
        calls = []

        def _push(store_public_id, payload, *, platform):
            calls.append(platform)
            if platform == "meta":
                return True
            raise RuntimeError("redis tiktok down")

        with patch("engine.apps.tracking.buffer.push_event_to_buffer", side_effect=_push):
            resp = self.client.post(
                "/tracking/event",
                data=json.dumps(self._valid_body("tt-fail")),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.api_key}",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(calls, ["meta", "tiktok"])

    @patch("engine.apps.tracking.views.cache.add", return_value=True)
    @patch("engine.apps.tracking.buffer.push_event_to_buffer", return_value=True)
    def test_ttp_ttclid_forwarded_to_tiktok_payload_not_meta(self, mock_push, _cache_add):
        captured = {}

        def _push(store_public_id, payload, *, platform):
            captured[platform] = payload
            return True

        mock_push.side_effect = _push
        self.client.post(
            "/tracking/event",
            data=json.dumps(self._valid_body("routing-1")),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key}",
        )
        self.assertIn("ttp", captured["tiktok"])
        self.assertIn("ttclid", captured["tiktok"])
        self.assertEqual(captured["tiktok"]["ttp"], "ttp-from-cookie")
        self.assertEqual(captured["tiktok"]["ttclid"], "ttclid-from-url")
        self.assertNotIn("ttp", captured["meta"])
        self.assertNotIn("ttclid", captured["meta"])

    @patch("engine.apps.tracking.views.cache.add", return_value=True)
    @patch("engine.apps.tracking.buffer.push_event_to_buffer", return_value=True)
    def test_fbp_fbc_in_meta_payload_not_in_tiktok(self, mock_push, _cache_add):
        captured = {}

        def _push(store_public_id, payload, *, platform):
            captured[platform] = payload
            return True

        mock_push.side_effect = _push
        self.client.post(
            "/tracking/event",
            data=json.dumps(self._valid_body("routing-2")),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key}",
        )
        self.assertEqual(captured["meta"].get("fbp"), "fb.1.meta")
        self.assertEqual(captured["meta"].get("fbc"), "fb.2.meta")
        self.assertNotIn("fbp", captured["tiktok"])
        self.assertNotIn("fbc", captured["tiktok"])

    @patch("engine.apps.tracking.views.cache.add", return_value=True)
    @patch("engine.apps.tracking.capi_payload.capi_enqueue_payload", wraps=capi_enqueue_payload)
    @patch("engine.apps.tracking.tiktok_payload.tiktok_enqueue_payload", wraps=tiktok_enqueue_payload)
    @patch("engine.apps.tracking.buffer.push_event_to_buffer", return_value=True)
    def test_enqueue_helpers_receive_expected_fields(
        self, mock_push, mock_tt_enqueue, mock_capi_enqueue, _cache_add
    ):
        self.client.post(
            "/tracking/event",
            data=json.dumps(self._valid_body("helpers-1")),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.api_key}",
        )
        self.assertTrue(mock_capi_enqueue.called)
        self.assertTrue(mock_tt_enqueue.called)
        capi_data = mock_capi_enqueue.call_args[0][0]
        tt_data = mock_tt_enqueue.call_args[0][0]
        self.assertIn("fbp", capi_data)
        self.assertIn("fbc", capi_data)
        self.assertNotIn("ttp", capi_data)
        self.assertNotIn("ttclid", capi_data)
        self.assertIn("ttp", tt_data)
        self.assertIn("ttclid", tt_data)
        self.assertNotIn("fbp", tt_data)
        self.assertNotIn("fbc", tt_data)
