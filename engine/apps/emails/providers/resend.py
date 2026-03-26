from __future__ import annotations

import json
import logging

import requests
from django.conf import settings

from .base import BaseEmailProvider

RESEND_API_URL = "https://api.resend.com/emails"
logger = logging.getLogger(__name__)


class ResendEmailProvider(BaseEmailProvider):
    """Send mail via Resend HTTP API."""

    def __init__(self, api_key: str | None = None, from_email: str | None = None):
        self.api_key = api_key if api_key is not None else getattr(settings, "RESEND_API_KEY", "") or ""
        configured = getattr(settings, "RESEND_FROM_EMAIL", "") or ""
        self.from_email = (from_email if from_email is not None else configured).strip() or (
            "onboarding@resend.dev"
        )

    def send(
        self,
        to_email: str,
        subject: str,
        html: str,
        text: str | None = None,
        *,
        from_email: str | None = None,
    ):
        logger.info(
            "RESEND_SEND_ENTERED to=%s has_api_key=%s from_email=%s",
            to_email,
            bool(self.api_key),
            (from_email or self.from_email),
        )
        if not self.api_key:
            raise RuntimeError("RESEND_API_KEY is not configured.")
        sender = (from_email or "").strip() or self.from_email

        payload: dict = {
            "from": sender,
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
        if text:
            payload["text"] = text

        logger.info("SENDING_EMAIL_VIA_RESEND to=%s from_email=%s", to_email, sender)
        response = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=30,
        )
        logger.info("RESEND_RESPONSE_RECEIVED to=%s status=%s", to_email, response.status_code)

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
