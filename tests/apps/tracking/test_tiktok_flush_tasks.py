"""TikTok flush tasks: coordinator dispatch, ack rules, HTTP outcomes (mirrors Meta flush patterns)."""

from __future__ import annotations

import hashlib
import json
import re
from unittest import TestCase
from unittest.mock import MagicMock, patch

import requests

from engine.apps.tracking.tiktok_flush_tasks import TIKTOK_EVENTS_API_URL


def _tiktok_integration_tuple(*, test_event_code: str = "") -> tuple:
    return ("PIXEL123", "access-secret", test_event_code, MagicMock())


class CoordinateTikTokFlushTests(TestCase):
    @patch("engine.apps.tracking.tiktok_flush_tasks.flush_store_tiktok")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    @patch("engine.apps.tracking.buffer.get_active_stores", return_value=["a", "b"])
    def test_dispatches_per_store(self, _mock_get_active, _mock_redis, mock_flush):
        from engine.apps.tracking.tiktok_flush_tasks import coordinate_tiktok_flush

        coordinate_tiktok_flush()
        self.assertEqual(mock_flush.apply_async.call_count, 2)
        mock_flush.apply_async.assert_any_call(args=["a"], queue="capi", ignore_result=True)
        mock_flush.apply_async.assert_any_call(args=["b"], queue="capi", ignore_result=True)

    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    @patch("engine.apps.tracking.buffer.get_active_stores", return_value=[])
    def test_no_stores_no_dispatch(self, _mock_get_active, _mock_redis):
        from engine.apps.tracking.tiktok_flush_tasks import coordinate_tiktok_flush

        with patch("engine.apps.tracking.tiktok_flush_tasks.flush_store_tiktok") as mock_flush:
            coordinate_tiktok_flush()
        mock_flush.apply_async.assert_not_called()

    @patch("engine.apps.tracking.flush_tasks.flush_store_capi.apply_async")
    @patch("engine.apps.tracking.tiktok_flush_tasks.flush_store_tiktok.apply_async")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    @patch("engine.apps.tracking.buffer.get_active_stores", return_value=["x"])
    def test_coordinate_never_dispatches_flush_store_capi(
        self, _gs, _redis, mock_tt_async, mock_capi_async
    ):
        from engine.apps.tracking.tiktok_flush_tasks import coordinate_tiktok_flush

        coordinate_tiktok_flush()
        mock_tt_async.assert_called_once()
        mock_capi_async.assert_not_called()


class FlushStoreTikTokTests(TestCase):
    def _valid_payload(self, **kwargs) -> dict:
        base = {
            "event_id": "e1",
            "event_name": "PageView",
            "event_time": 1700000000,
            "event_source_url": "https://example.com/p",
            "user_agent": "Mozilla/5.0",
            "client_ip_address": "1.2.3.4",
            "value": 10.0,
            "currency": "USD",
        }
        base.update(kwargs)
        return base

    @patch("engine.apps.tracking.buffer.remove_store_from_active")
    @patch("engine.apps.tracking.buffer.read_pending_events", return_value=[])
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_empty_stream_removes_active_no_http(self, _r, mock_post, mock_read, mock_remove):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        flush_store_tiktok.run("store-1")
        mock_read.assert_called_once()
        mock_remove.assert_called_once()
        self.assertEqual(mock_remove.call_args.kwargs.get("platform"), "tiktok")
        mock_post.assert_not_called()

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_bad_json_acked_only_bad_ids(
        self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog
    ):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid_bad = b"99-0"
        mid_good = b"100-0"
        good_body = json.dumps(self._valid_payload())
        mock_read.return_value = [
            (mid_bad, {b"payload": b"{not-json"}),
            (mid_good, {b"payload": good_body.encode()}),
        ]
        mock_load.return_value = _tiktok_integration_tuple()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0}
        mock_post.return_value = mock_resp

        flush_store_tiktok.run("store-1")

        self.assertTrue(mock_ack.called)
        self.assertTrue(any(mid_bad in c[0][2] for c in mock_ack.call_args_list if len(c[0]) > 2))
        mock_post.assert_called_once()

    @patch("engine.apps.tracking.tiktok_flush_tasks.logger")
    @patch("engine.apps.tracking.buffer.remove_store_from_active")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration", return_value=None)
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_no_integration_acks_all_and_removes_active_no_error_log(
        self, _gr, _mock_load, mock_read, mock_ack, mock_remove, mock_logger
    ):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"1-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(self._valid_payload()).encode()})]

        flush_store_tiktok.run("store-1")

        mock_ack.assert_called()
        self.assertTrue(any(mid in c[0][2] for c in mock_ack.call_args_list))
        mock_remove.assert_called_once()
        self.assertEqual(mock_remove.call_args.kwargs.get("platform"), "tiktok")
        mock_logger.error.assert_not_called()

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_4xx_acks_without_retry(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"2-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(self._valid_payload()).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_resp = MagicMock(status_code=400, json=lambda: {})
        mock_post.return_value = mock_resp

        flush_store_tiktok.run("store-1")

        self.assertTrue(any(mid in c[0][2] for c in mock_ack.call_args_list))

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_network_timeout_retries_no_ack(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"3-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(self._valid_payload()).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.side_effect = requests.Timeout()

        with patch.object(flush_store_tiktok, "retry", side_effect=RuntimeError("retry")) as mock_retry:
            with self.assertRaises(RuntimeError):
                flush_store_tiktok.run("store-1")

        mock_retry.assert_called_once()
        mock_ack.assert_not_called()

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_connection_error_retries_no_ack(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"4-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(self._valid_payload()).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.side_effect = requests.ConnectionError("boom")

        with patch.object(flush_store_tiktok, "retry", side_effect=RuntimeError("retry")) as mock_retry:
            with self.assertRaises(RuntimeError):
                flush_store_tiktok.run("store-1")

        mock_retry.assert_called_once()
        mock_ack.assert_not_called()

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_success_acks_all_ids(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"5-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(self._valid_payload()).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})

        flush_store_tiktok.run("store-1")

        ack_ids = [c[0][2] for c in mock_ack.call_args_list if len(c[0]) > 2]
        self.assertTrue(any(mid in batch for batch in ack_ids))

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_post_uses_access_token_header_not_body(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"6-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(self._valid_payload()).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})

        flush_store_tiktok.run("store-1")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], TIKTOK_EVENTS_API_URL)
        self.assertEqual(kwargs["headers"].get("Access-Token"), "access-secret")
        body = json.loads(kwargs["data"].decode("utf-8"))
        self.assertNotIn("access_token", body)
        self.assertEqual(body["pixel_code"], "PIXEL123")
        self.assertEqual(body["event_source"], "web")
        self.assertIsInstance(body["data"], list)
        self.assertEqual(len(body["data"]), 1)

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_purchase_maps_to_place_an_order(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"7-0"
        pl = self._valid_payload(event_name="Purchase", event_id="ord-1")
        mock_read.return_value = [(mid, {b"payload": json.dumps(pl).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})

        flush_store_tiktok.run("store-1")
        body = json.loads(mock_post.call_args[1]["data"].decode("utf-8"))
        self.assertEqual(body["data"][0]["event"], "PlaceAnOrder")

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_pageview_maps_to_pageview_casing(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"8-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(self._valid_payload()).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})

        flush_store_tiktok.run("store-1")
        body = json.loads(mock_post.call_args[1]["data"].decode("utf-8"))
        self.assertEqual(body["data"][0]["event"], "Pageview")

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_add_to_cart_unchanged(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"9-0"
        pl = self._valid_payload(event_name="AddToCart", event_id="c1")
        mock_read.return_value = [(mid, {b"payload": json.dumps(pl).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})

        flush_store_tiktok.run("store-1")
        body = json.loads(mock_post.call_args[1]["data"].decode("utf-8"))
        self.assertEqual(body["data"][0]["event"], "AddToCart")

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_user_hashes_email_and_phone(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"10-0"
        pl = self._valid_payload(
            event_name="Purchase",
            event_id="p1",
            email="Test@Example.com",
            phone="+1 (555) 123-4567",
        )
        mock_read.return_value = [(mid, {b"payload": json.dumps(pl).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})

        flush_store_tiktok.run("store-1")
        body = json.loads(mock_post.call_args[1]["data"].decode("utf-8"))
        user = body["data"][0]["user"]
        want_em = hashlib.sha256("test@example.com".encode()).hexdigest()
        digits = re.sub(r"[^\d]", "", "+1 (555) 123-4567")
        want_ph = hashlib.sha256(digits.encode()).hexdigest()
        self.assertEqual(user["email"], want_em)
        self.assertEqual(user["phone_number"], want_ph)

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_ttp_ttclid_plain_ip_ua_plain(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"11-0"
        pl = self._valid_payload(
            event_id="p2",
            ttp="ttp-plain",
            ttclid="ttclid-plain",
            client_ip_address="8.8.8.8",
        )
        mock_read.return_value = [(mid, {b"payload": json.dumps(pl).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})

        flush_store_tiktok.run("store-1")
        user = json.loads(mock_post.call_args[1]["data"].decode("utf-8"))["data"][0]["user"]
        self.assertEqual(user["ttp"], "ttp-plain")
        self.assertEqual(user["ttclid"], "ttclid-plain")
        self.assertEqual(user["ip"], "8.8.8.8")
        self.assertEqual(user["user_agent"], "Mozilla/5.0")

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_test_event_code_in_body_when_set(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"12-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(self._valid_payload()).encode()})]
        mock_load.return_value = _tiktok_integration_tuple(test_event_code="TESTCODE42")
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})

        flush_store_tiktok.run("store-1")
        body = json.loads(mock_post.call_args[1]["data"].decode("utf-8"))
        self.assertEqual(body.get("test_event_code"), "TESTCODE42")

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_test_event_code_omitted_when_empty(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"13-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(self._valid_payload()).encode()})]
        mock_load.return_value = _tiktok_integration_tuple(test_event_code="")
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})

        flush_store_tiktok.run("store-1")
        body = json.loads(mock_post.call_args[1]["data"].decode("utf-8"))
        self.assertNotIn("test_event_code", body)

    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_read_and_ack_only_tiktok_platform(self, _gr, mock_read, mock_ack):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mock_read.return_value = []
        flush_store_tiktok.run("store-z")
        mock_read.assert_called_once()
        self.assertEqual(mock_read.call_args.kwargs.get("platform"), "tiktok")

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_flush_never_calls_buffer_with_meta_platform(
        self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog
    ):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        mid = b"14-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(self._valid_payload()).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})

        flush_store_tiktok.run("store-1")

        for c in mock_read.call_args_list:
            self.assertEqual(c.kwargs.get("platform"), "tiktok")
        for c in mock_ack.call_args_list:
            self.assertEqual(c.kwargs.get("platform"), "tiktok")


class FlushStoreTikTokViewContentTests(TestCase):
    """ViewContent / InitiateCheckout mapping unchanged (identity)."""

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_view_content_unchanged(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        pl = {
            "event_id": "v1",
            "event_name": "ViewContent",
            "event_time": 1700000000,
            "event_source_url": "https://example.com/p",
            "user_agent": "Mozilla/5.0",
            "client_ip_address": "1.1.1.1",
        }
        mid = b"20-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(pl).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})
        flush_store_tiktok.run("store-1")
        body = json.loads(mock_post.call_args[1]["data"].decode("utf-8"))
        self.assertEqual(body["data"][0]["event"], "ViewContent")

    @patch("engine.apps.tracking.tiktok_flush_tasks._try_db_log")
    @patch("engine.apps.tracking.tiktok_flush_tasks.requests.post")
    @patch("engine.apps.tracking.tiktok_flush_tasks._load_tiktok_integration")
    @patch("engine.apps.tracking.buffer.ack_events")
    @patch("engine.apps.tracking.buffer.read_pending_events")
    @patch("engine.apps.tracking.tiktok_flush_tasks._get_redis")
    def test_initiate_checkout_unchanged(self, _gr, mock_read, mock_ack, mock_load, mock_post, _dblog):
        from engine.apps.tracking.tiktok_flush_tasks import flush_store_tiktok

        pl = {
            "event_id": "ic1",
            "event_name": "InitiateCheckout",
            "event_time": 1700000000,
            "event_source_url": "https://example.com/c",
            "user_agent": "Mozilla/5.0",
            "client_ip_address": "1.1.1.1",
        }
        mid = b"21-0"
        mock_read.return_value = [(mid, {b"payload": json.dumps(pl).encode()})]
        mock_load.return_value = _tiktok_integration_tuple()
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"code": 0})
        flush_store_tiktok.run("store-1")
        body = json.loads(mock_post.call_args[1]["data"].decode("utf-8"))
        self.assertEqual(body["data"][0]["event"], "InitiateCheckout")
