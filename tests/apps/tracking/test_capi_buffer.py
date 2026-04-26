import decimal
import json
from unittest import TestCase
from unittest.mock import patch

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


class PushEventToBufferTests(TestCase):
    def test_push_event_to_buffer_handles_decimal_payload(self):
        fake_redis = _FakeRedis()
        payload = {
            "event_name": "Purchase",
            "value": decimal.Decimal("12.34"),
        }

        with patch("engine.apps.tracking.buffer._get_redis", return_value=fake_redis):
            pushed = buffer.push_event_to_buffer("store-123", payload)

        self.assertTrue(pushed)
        self.assertEqual(len(fake_redis.xadd_calls), 1)
        _key, fields, _maxlen, _approximate = fake_redis.xadd_calls[0]
        decoded = json.loads(fields["payload"])
        self.assertEqual(decoded["value"], 12.34)
