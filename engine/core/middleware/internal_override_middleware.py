from __future__ import annotations

from dataclasses import dataclass

from django.utils.deprecation import MiddlewareMixin

from engine.core.authz import can_enable_internal_override
from engine.core.client_ip import get_client_ip


@dataclass(frozen=True)
class AuthContext:
    internal_override_enabled: bool
    client_ip: str


class InternalOverrideMiddleware(MiddlewareMixin):
    """
    Build trusted override context from authenticated identity + allowlisted IP.
    """

    def process_request(self, request):
        if request.path in {"/health", "/health/"}:
            request.auth_context = AuthContext(
                internal_override_enabled=False,
                client_ip=get_client_ip(request),
            )
            return None
        user = getattr(request, "user", None)
        client_ip = get_client_ip(request)
        enabled = can_enable_internal_override(user=user, client_ip=client_ip)
        request.auth_context = AuthContext(
            internal_override_enabled=enabled,
            client_ip=client_ip,
        )
        return None
