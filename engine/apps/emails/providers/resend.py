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
        logger.info(
            "RESEND_PROVIDER_INIT api_key_present=%s configured_from_present=%s effective_from=%s",
            bool(self.api_key),
            bool(configured.strip()),
            self.from_email,
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
        if not self.api_key:
            logger.error("RESEND_API_KEY_MISSING")
            raise RuntimeError("RESEND_API_KEY is not configured.")
        sender = (from_email or "").strip() or self.from_email
        if sender == "onboarding@resend.dev":
            logger.warning("RESEND_FALLBACK_SENDER_IN_USE sender=%s", sender)

        payload: dict = {
            "from": sender,
            "to": [to_email],
            "subject": subject,
            "html": html,
        }
        if text:
            payload["text"] = text

        logger.info(
            "RESEND_REQUEST_START to=%s subject_length=%s sender=%s has_text=%s",
            to_email,
            len(subject or ""),
            sender,
            bool(text),
        )
        response = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=30,
        )
        logger.info("RESEND_RESPONSE_RECEIVED status=%s to=%s", response.status_code, to_email)

        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                logger.error(
                    "RESEND_ERROR_NON_JSON status=%s body=%r to=%s",
                    response.status_code,
                    response.text,
                    to_email,
                )
                raise RuntimeError(
                    f"Resend API error {response.status_code}: {response.text!r}"
                ) from None
            if isinstance(detail, dict) and detail.get("message"):
                # Resend returns e.g. 403 when test keys may only email the account owner;
                # surface their message in EmailLog.error_message.
                logger.error(
                    "RESEND_ERROR_JSON status=%s message=%s to=%s",
                    response.status_code,
                    detail["message"],
                    to_email,
                )
                raise RuntimeError(
                    f"Resend API error {response.status_code}: {detail['message']}"
                ) from None
            logger.error(
                "RESEND_ERROR_JSON_UNKNOWN status=%s detail=%r to=%s",
                response.status_code,
                detail,
                to_email,
            )
            raise RuntimeError(f"Resend API error {response.status_code}: {detail!r}")

        data = response.json() if response.content else {}
        logger.info("RESEND_SEND_SUCCESS to=%s response_id=%s", to_email, data.get("id"))
        return data
