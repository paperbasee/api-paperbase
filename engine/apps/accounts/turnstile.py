"""Cloudflare Turnstile server-side verification."""

from __future__ import annotations

import logging

import requests
from django.conf import settings

from engine.core.client_ip import get_client_ip

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile_request(request) -> tuple[bool, str | None]:
    """
    Validate the Turnstile token on an unauthenticated POST (login/register).

    Returns (ok, error_detail). When verification is skipped (tests or no secret
    configured), returns (True, None).
    """
    if getattr(settings, "TESTING", False):
        return True, None

    secret = (getattr(settings, "TURNSTILE_SECRET_KEY", None) or "").strip()
    if not secret:
        return True, None

    raw = request.data.get("cf_turnstile_response") or request.data.get("cf-turnstile-response")
    token = (raw or "").strip() if isinstance(raw, str) else ""
    if not token:
        return False, "Turnstile verification failed."

    remote_ip = get_client_ip(request) or ""
    try:
        r = requests.post(
            SITEVERIFY_URL,
            data={"secret": secret, "response": token, "remoteip": remote_ip},
            timeout=10,
        )
        r.raise_for_status()
        body = r.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Turnstile siteverify request failed: %s", e)
        return False, "Turnstile verification failed."

    if body.get("success") is True:
        return True, None
    return False, "Turnstile verification failed."
