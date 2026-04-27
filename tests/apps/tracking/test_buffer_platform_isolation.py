"""Meta vs TikTok Redis buffer isolation (fakeredis)."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

import fakeredis
from django.test import SimpleTestCase

from engine.apps.tracking import buffer as buf


class BufferPlatformIsolationTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.r = fakeredis.FakeRedis(decode_responses=True)
        self._patch_redis = patch.object(buf, "_get_redis", return_value=self.r)
        self._patch_redis.start()
        self.addCleanup(self._patch_redis.stop)
        self.r.flushall()

    def _legacy_key_fragment_forbidden(self) -> None:
        legacy = "capi:stream:"
        for k in self.r.keys():
            self.assertNotIn(legacy, k, msg=f"unexpected legacy fragment in key {k!r}")

    def test_push_meta_writes_meta_stream_only(self):
        buf.push_event_to_buffer("store_a", {"x": 1}, platform="meta")
        self.assertEqual(self.r.xlen("capi:meta:stream:store_a"), 1)
        self.assertFalse(self.r.exists("capi:tiktok:stream:store_a"))
        self._legacy_key_fragment_forbidden()

    def test_push_tiktok_writes_tiktok_stream_only(self):
        buf.push_event_to_buffer("store_a", {"x": 1}, platform="tiktok")
        self.assertEqual(self.r.xlen("capi:tiktok:stream:store_a"), 1)
        self.assertFalse(self.r.exists("capi:meta:stream:store_a"))
        self._legacy_key_fragment_forbidden()

    def test_active_stores_sets_are_separate(self):
        buf.push_event_to_buffer("only_meta", {}, platform="meta")
        buf.push_event_to_buffer("only_tt", {}, platform="tiktok")
        meta_ids = set(buf.get_active_stores(self.r, platform="meta"))
        tt_ids = set(buf.get_active_stores(self.r, platform="tiktok"))
        self.assertEqual(meta_ids, {"only_meta"})
        self.assertEqual(tt_ids, {"only_tt"})
        self.assertNotIn("only_tt", meta_ids)
        self.assertNotIn("only_meta", tt_ids)

    def test_get_active_stores_meta_excludes_tiktok_only_store(self):
        buf.push_event_to_buffer("tiktok_only", {}, platform="tiktok")
        self.assertEqual(buf.get_active_stores(self.r, platform="meta"), [])

    def test_get_active_stores_tiktok_excludes_meta_only_store(self):
        buf.push_event_to_buffer("meta_only", {}, platform="meta")
        self.assertEqual(buf.get_active_stores(self.r, platform="tiktok"), [])

    def test_meta_event_store_a_not_in_store_b_meta_stream(self):
        buf.push_event_to_buffer("store_a", {"id": "a"}, platform="meta")
        self.assertEqual(self.r.xlen("capi:meta:stream:store_b"), 0)

    def test_tiktok_event_store_a_not_in_store_a_meta_stream(self):
        buf.push_event_to_buffer("store_a", {"id": "tt"}, platform="tiktok")
        self.assertFalse(self.r.exists("capi:meta:stream:store_a"))

    def test_tiktok_event_store_a_not_in_store_b_tiktok_stream(self):
        buf.push_event_to_buffer("store_a", {"id": "tt"}, platform="tiktok")
        self.assertEqual(self.r.xlen("capi:tiktok:stream:store_b"), 0)

    def test_consumer_groups_created_per_platform(self):
        buf.push_event_to_buffer("s1", {}, platform="meta")
        buf.push_event_to_buffer("s1", {}, platform="tiktok")
        meta_groups = self.r.xinfo_groups("capi:meta:stream:s1")
        tt_groups = self.r.xinfo_groups("capi:tiktok:stream:s1")
        self.assertEqual({g["name"] for g in meta_groups}, {"capi-meta-workers"})
        self.assertEqual({g["name"] for g in tt_groups}, {"capi-tiktok-workers"})
        self.assertNotIn("capi-tiktok-workers", {g["name"] for g in meta_groups})
        self.assertNotIn("capi-meta-workers", {g["name"] for g in tt_groups})

    def test_early_flush_meta_dispatches_flush_store_capi_not_tiktok(self):
        old = buf._BUFFER_PLATFORM_REGISTRY["meta"]
        buf._BUFFER_PLATFORM_REGISTRY["meta"] = replace(old, early_flush_threshold=1)
        try:
            with patch("engine.apps.tracking.flush_tasks.flush_store_capi.apply_async") as mock_capi:
                with patch("engine.apps.tracking.tiktok_flush_tasks.flush_store_tiktok.apply_async") as mock_tt:
                    buf.push_event_to_buffer("early_m", {"e": 1}, platform="meta")
            mock_capi.assert_called_once_with(args=["early_m"], queue="capi", ignore_result=True)
            mock_tt.assert_not_called()
        finally:
            buf._BUFFER_PLATFORM_REGISTRY["meta"] = old

    def test_early_flush_tiktok_dispatches_flush_store_tiktok_not_capi(self):
        old = buf._BUFFER_PLATFORM_REGISTRY["tiktok"]
        buf._BUFFER_PLATFORM_REGISTRY["tiktok"] = replace(old, early_flush_threshold=1)
        try:
            with patch("engine.apps.tracking.flush_tasks.flush_store_capi.apply_async") as mock_capi:
                with patch("engine.apps.tracking.tiktok_flush_tasks.flush_store_tiktok.apply_async") as mock_tt:
                    buf.push_event_to_buffer("early_t", {"e": 1}, platform="tiktok")
            mock_tt.assert_called_once_with(args=["early_t"], queue="capi", ignore_result=True)
            mock_capi.assert_not_called()
        finally:
            buf._BUFFER_PLATFORM_REGISTRY["tiktok"] = old

    def test_ack_meta_does_not_remove_tiktok_pending_message(self):
        buf.push_event_to_buffer("s", {"n": "meta"}, platform="meta")
        buf.push_event_to_buffer("s", {"n": "tt"}, platform="tiktok")
        meta_entries = buf.read_pending_events(self.r, "s", "cons1", count=10, platform="meta")
        self.assertEqual(len(meta_entries), 1)
        buf.ack_events(self.r, "s", [meta_entries[0][0]], platform="meta")
        tt_entries = buf.read_pending_events(self.r, "s", "cons2", count=10, platform="tiktok")
        self.assertEqual(len(tt_entries), 1)
        fields = tt_entries[0][1]
        raw = fields.get("payload") or fields.get(b"payload")
        if isinstance(raw, bytes):
            raw = raw.decode()
        self.assertEqual(json.loads(raw).get("n"), "tt")
        self._legacy_key_fragment_forbidden()

    def test_remove_store_from_active_meta_does_not_touch_tiktok_set(self):
        self.r.sadd("capi:meta:active_stores", "sid")
        self.r.sadd("capi:tiktok:active_stores", "sid")
        buf.remove_store_from_active(self.r, "sid", platform="meta")
        self.assertEqual(self.r.smembers("capi:tiktok:active_stores"), {"sid"})
        self.assertEqual(self.r.smembers("capi:meta:active_stores"), set())

    def test_keys_after_operations_exclude_legacy_prefix(self):
        buf.push_event_to_buffer("x", {}, platform="meta")
        buf.push_event_to_buffer("y", {}, platform="tiktok")
        self._legacy_key_fragment_forbidden()
