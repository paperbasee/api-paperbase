"""Send mail via Django's EMAIL_BACKEND (e.g. locmem in tests)."""

from __future__ import annotations

from django.core.mail import EmailMultiAlternatives

from engine.apps.emails.router import format_from_with_display_name, resolve_email_sender

from .base import BaseEmailProvider


class DjangoCoreMailProvider(BaseEmailProvider):
    """
    Uses django.core.mail so EMAIL_BACKEND controls delivery (locmem, console, smtp, …).
    Avoids external APIs (e.g. Resend) during pytest / manage.py test.
    """

    provider_key = "django"

    def send(
        self,
        email_type: str,
        to_email: str,
        subject: str,
        html: str,
        text: str | None = None,
        *,
        from_email: str | None = None,
    ) -> None:
        override = (from_email or "").strip()
        resolved_from = (
            format_from_with_display_name(override) if override else resolve_email_sender(email_type)
        )

        plain = (text or "").strip()
        if not plain:
            plain = "(HTML email — use an HTML-capable client.)" if (html or "").strip() else " "

        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain,
            from_email=resolved_from,
            to=[to_email],
        )
        if (html or "").strip():
            msg.attach_alternative(html, "text/html")
        msg.send(fail_silently=False)
