from __future__ import annotations

import json

import requests
from django.conf import settings

from engine.apps.emails.router import format_from_with_display_name, resolve_email_sender

from .base import BaseEmailProvider

RESEND_API_URL = "https://api.resend.com/emails"


class ResendEmailProvider(BaseEmailProvider):
    """Send mail via Resend HTTP API."""

    provider_key = "resend"

    def __init__(self, api_key: str | None = None, from_email: str | None = None):
        self.api_key = api_key if api_key is not None else getattr(settings, "RESEND_API_KEY", "") or ""
        self.from_email = (from_email or "").strip()

    def send(
        self,
        email_type: str,
        to_email: str,
        subject: str,
        html: str,
        text: str | None = None,
        *,
        from_email: str | None = None,
    ):
        if not self.api_key:
            raise RuntimeError("RESEND_API_KEY is not configured.")
        override = (from_email or "").strip()
        sender = (
            format_from_with_display_name(override) if override else resolve_email_sender(email_type)
        )

        payload: dict = {
            "from": sender,
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
        if text:
            payload["text"] = text

        response = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=30,
        )

        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                raise RuntimeError(
                    f"Resend API error {response.status_code}: {response.text!r}"
                ) from None
            if isinstance(detail, dict) and detail.get("message"):
                # Resend returns e.g. 403 when test keys may only email the account owner;
                # surface their message in EmailLog.error_message.
                raise RuntimeError(
                    f"Resend API error {response.status_code}: {detail['message']}"
                ) from None
            raise RuntimeError(f"Resend API error {response.status_code}: {detail!r}")

        return response.json() if response.content else {}
