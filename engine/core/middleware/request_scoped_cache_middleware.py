from __future__ import annotations

from django.utils.deprecation import MiddlewareMixin

from engine.core.request_context import reset_request_scoped_caches


class RequestScopedCacheMiddleware(MiddlewareMixin):
    """Reset ContextVar-backed request caches so workers never leak across requests."""

    def process_request(self, request):
        reset_request_scoped_caches()
        return None

    def process_response(self, request, response):
        reset_request_scoped_caches()
        return response

    def process_exception(self, request, exception):
        reset_request_scoped_caches()
        return None
