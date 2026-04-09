from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin


class SubscriptionEnforcementMiddleware(MiddlewareMixin):
    """
    Global safety-net: deny access for authenticated non-superusers without
    an active subscription on protected paths.

    Works with Django session auth (request.user set by AuthenticationMiddleware).
    DRF JWT-auth paths are also covered by permission classes (IsDashboardUser,
    IsStoreStaff, IsStoreAdmin) as defence-in-depth.
    """

    EXEMPT_PREFIXES = (
        "/health",
        "/api/v1/auth/",
    )

    def _is_exempt(self, path: str) -> bool:
        if path in {"/health", "/health/"}:
            return True
        for prefix in self.EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return True
        admin_path = getattr(settings, "ADMIN_URL_PATH", "admin/")
        if path.startswith(f"/{admin_path}") or path.startswith(f"/{admin_path.lstrip('/')}"):
            return True
        return False

    def process_view(self, request, view_func, view_args, view_kwargs):
        if self._is_exempt(request.path):
            return None

        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return None

        if getattr(user, "is_superuser", False):
            return None

        if getattr(request, "api_key", None):
            return None

        if getattr(view_func, "cls", None):
            from rest_framework.views import APIView

            if issubclass(view_func.cls, APIView):
                return None

        from engine.apps.billing.services import get_active_subscription

        if get_active_subscription(user) is None:
            return JsonResponse(
                {"detail": "An active subscription is required."},
                status=403,
            )

        return None
