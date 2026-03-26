from __future__ import annotations

import copy

from django.template import Context, Template
from django.utils import timezone

from .models import EmailLog, EmailTemplate
from .providers.base import BaseEmailProvider
from .providers.resend import ResendEmailProvider

_ERROR_MAX_LEN = 8000


def _render(template_string: str, context: dict) -> str:
    return Template(template_string).render(Context(context))


def get_email_provider() -> BaseEmailProvider:
    return ResendEmailProvider()


def send_email(
    email_type: str,
    to_email: str,
    context: dict | None = None,
    *,
    provider: BaseEmailProvider | None = None,
    from_email: str | None = None,
) -> EmailLog:
    """
    Load template by type, render bodies, persist EmailLog, send via provider.

    Intended to be called from Celery tasks (not from HTTP views directly).
    """
    ctx = copy.deepcopy(context) if context else {}
    template = EmailTemplate.objects.get(type=email_type, is_active=True)

    subject = _render(template.subject, ctx)
    html = _render(template.html_body, ctx)
    text = _render(template.text_body, ctx) if (template.text_body or "").strip() else None

    log = EmailLog.objects.create(
        to_email=to_email,
        type=email_type,
        status=EmailLog.Status.PENDING,
        provider="resend",
        metadata=ctx,
    )

    mailer = provider or get_email_provider()
    try:
        mailer.send(to_email, subject, html, text, from_email=from_email)
    except Exception as exc:  # noqa: BLE001 — record any failure on the log
        err = str(exc)[:_ERROR_MAX_LEN]
        log.status = EmailLog.Status.FAILED
        log.error_message = err
        log.save(update_fields=["status", "error_message"])
        raise

    log.status = EmailLog.Status.SENT
    log.sent_at = timezone.now()
    log.error_message = ""
    log.save(update_fields=["status", "sent_at", "error_message"])
    return log
