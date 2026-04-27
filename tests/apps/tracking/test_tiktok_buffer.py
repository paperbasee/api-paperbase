import decimal
import json
from unittest import TestCase
from unittest.mock import MagicMock, patch

from engine.apps.tracking import buffer


class _FakeRedis:
    def __init__(self):
        self.xadd_calls = []

    def xadd(self, key, fields, maxlen=None, approximate=None):
        self.xadd_calls.append((key, fields, maxlen, approximate))

    def sadd(self, *_args, **_kwargs):
        return 1

    def expire(self, *_args, **_kwargs):
        return True

    def xgroup_create(self, *_args, **_kwargs):
        return True

    def xlen(self, *_args, **_kwargs):
        return 0


class TikTokPushEventToBufferTests(TestCase):
    def test_push_uses_tiktok_stream_key(self):
        fake_redis = _FakeRedis()
        payload = {"event_name": "Purchase", "value": decimal.Decimal("1.00")}

        with patch("engine.apps.tracking.buffer._get_redis", return_value=fake_redis):
            pushed = buffer.push_event_to_buffer("store-xyz", payload, platform="tiktok")

        self.assertTrue(pushed)
        key, fields, maxlen, _ = fake_redis.xadd_calls[0]
        self.assertEqual(key, "capi:tiktok:stream:store-xyz")
        self.assertEqual(maxlen, buffer.MAX_STREAM_LEN)
        self.assertEqual(json.loads(fields["payload"])["value"], 1.0)

    def test_early_flush_dispatches_flush_store_tiktok(self):
        from engine.apps.tracking import tiktok_flush_tasks as tft

        fake_redis = MagicMock()
        fake_redis.xlen.return_value = buffer.TIKTOK_EARLY_FLUSH_THRESHOLD

        with patch("engine.apps.tracking.buffer._get_redis", return_value=fake_redis):
            with patch.object(tft.flush_store_tiktok, "apply_async") as mock_apply:
                buffer.push_event_to_buffer("s1", {"a": 1}, platform="tiktok")

        mock_apply.assert_called_once_with(args=["s1"], queue="capi", ignore_result=True)
