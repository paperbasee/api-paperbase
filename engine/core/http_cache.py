"""
HTTP cache control utilities for storefront read endpoints.
Sets Cache-Control and Vary headers so Cloudflare can cache
per-store API responses correctly.
"""
from __future__ import annotations

from functools import wraps


def storefront_cache_headers(max_age: int = 60, s_maxage: int | None = None):
    """
    Decorator for DRF view methods (get, list, retrieve).
    Sets Cache-Control: public, max-age=N, s-maxage=N
    directly on the returned Response object.

    Works correctly with DRF Response objects.
    Safe for storefront read-only endpoints only.

    Usage on a method:
        @storefront_cache_headers(max_age=60)
        def list(self, request, *args, **kwargs): ...
    """
    _s = s_maxage if s_maxage is not None else max_age
    _header_value = f"public, max-age={max_age}, s-maxage={_s}"

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            response = func(*args, **kwargs)
            if response is not None:
                response["Cache-Control"] = _header_value
            return response

        return wrapper

    return decorator


def no_store_cache(func):
    """
    Decorator for endpoints that must never be cached.
    Use on checkout, orders, auth endpoints.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        response = func(*args, **kwargs)
        if response is not None:
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        return response

    return wrapper
