from __future__ import annotations

from django.conf import settings


def client_ip_from_request(request) -> str:
    """
    Best-effort client IP extraction for CAPI user_data.

    Uses settings.TRUSTED_IP_HEADER (default HTTP_X_FORWARDED_FOR) when present,
    falling back to REMOTE_ADDR. When the trusted header is a comma-separated
    list, the left-most value is used.
    """

    header_key = (getattr(settings, "TRUSTED_IP_HEADER", "") or "HTTP_X_FORWARDED_FOR").strip()
    ip = ""
    try:
        if header_key:
            ip = (request.META.get(header_key, "") or "").strip()
    except Exception:
        ip = ""
    if ip:
        ip = ip.split(",")[0].strip()
    if not ip:
        try:
            ip = (request.META.get("REMOTE_ADDR", "") or "").strip()
        except Exception:
            ip = ""
    return ip

