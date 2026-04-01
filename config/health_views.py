"""Liveness endpoint with scoped throttling (no sensitive logging)."""

from __future__ import annotations

import logging
from time import perf_counter

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "health"

    def get(self, request):
        start = perf_counter()
        elapsed_ms = (perf_counter() - start) * 1000
        logger.debug("health check %.2f ms", elapsed_ms)
        return Response({"status": "ok"})
