"""
Atomic fixed-window counters for rate limiting.

Redis: single Lua script (INCR + EXPIRE on first hit) avoids races on cold keys.
Non-Redis caches (e.g. LocMem in tests): fall back to incr / set pattern.

This does not implement a sliding window; boundary bursts remain possible.

If the cache backend errors (e.g. Redis unreachable), incr_under_limit fails open
(returns True) so storefront traffic is not taken down by the limiter.
"""

from __future__ import annotations

import logging

from django.core.cache.backends.base import BaseCache
from django.core.cache.backends.redis import RedisCache

logger = logging.getLogger(__name__)

_LUA_INCR_EXPIRE = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return c
"""


def fixed_window_increment(cache: BaseCache, raw_key: str, window_seconds: int) -> int:
    """
    Increment the counter for raw_key in a fixed window of window_seconds.

    Returns the new counter value after increment.
    """
    if window_seconds <= 0:
        return 0

    if isinstance(cache, RedisCache):
        redis_key = cache.make_and_validate_key(raw_key, version=None)
        client = cache._cache.get_client(redis_key, write=True)
        return int(client.eval(_LUA_INCR_EXPIRE, 1, redis_key, str(int(window_seconds))))

    try:
        return cache.incr(raw_key)
    except ValueError:
        cache.set(raw_key, 1, window_seconds)
        return 1


def incr_under_limit(cache: BaseCache, raw_key: str, window_seconds: int, limit: int) -> bool:
    """
    Return True if the count after increment is within limit (inclusive).

    On cache/Redis errors, logs a warning and returns True (fail open).
    """
    if limit <= 0:
        return True
    try:
        n = fixed_window_increment(cache, raw_key, window_seconds)
        return n <= limit
    except Exception:
        logger.warning(
            "rate_limit cache error; fail open (allow request)",
            exc_info=True,
            extra={"rate_limit_key": raw_key},
        )
        return True
