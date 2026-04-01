"""
Trusted client IP for rate limiting, internal override, and security checks.

Uses the same forwarded-header rules as Django REST framework's BaseThrottle.get_ident
when TRUSTED_IP_HEADER is HTTP_X_FORWARDED_FOR and REST_FRAMEWORK["NUM_PROXIES"] is set.

Ingress must strip or overwrite client-supplied X-Forwarded-For; only trusted proxies
should append to the chain.
"""

from __future__ import annotations

from django.conf import settings


def get_client_ip(request) -> str:
    """
    Resolve the client IP from REMOTE_ADDR and the configured trusted header.

    Mirrors DRF 3.15 BaseThrottle.get_ident semantics for NUM_PROXIES handling.
    """
    header_name = getattr(settings, "TRUSTED_IP_HEADER", "HTTP_X_FORWARDED_FOR") or "HTTP_X_FORWARDED_FOR"
    forwarded = (request.META.get(header_name) or "").strip()
    remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()

    rf = getattr(settings, "REST_FRAMEWORK", None) or {}
    num_proxies = rf.get("NUM_PROXIES", None)

    if num_proxies is not None:
        if num_proxies == 0 or not forwarded:
            return remote_addr
        addrs = [a.strip() for a in forwarded.split(",") if a.strip()]
        if not addrs:
            return remote_addr
        take = min(num_proxies, len(addrs))
        return addrs[-take]

    # Match DRF when NUM_PROXIES is None: full XFF string without spaces, else REMOTE_ADDR.
    if header_name == "HTTP_X_FORWARDED_FOR":
        return "".join(forwarded.split()) if forwarded else remote_addr
    return forwarded.split(",")[0].strip() if forwarded else remote_addr
