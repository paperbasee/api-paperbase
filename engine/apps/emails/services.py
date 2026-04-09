from __future__ import annotations

import copy
from datetime import date, datetime

from django.conf import settings
from django.template import Context, Template
from django.utils import timezone

from engine.utils.time import format_bd_date, format_bd_with_label

from .models import EmailLog, EmailTemplate
from .template_catalog import DEFAULT_EMAIL_TEMPLATES

from .providers.base import BaseEmailProvider
from .providers.django_mail import DjangoCoreMailProvider
from .providers.resend import ResendEmailProvider

_ERROR_MAX_LEN = 8000


def _normalize_email_context(obj):
    """
    Recursively format datetime/date values for templates.
    Datetimes: DD-MM-YYYY HH:MM (GMT+6). Dates: DD-MM-YYYY.
    datetime is handled before date because datetime is a subclass of date.
    """
    if isinstance(obj, datetime):
        return format_bd_with_label(obj)
    if type(obj) is date:
        return format_bd_date(obj)
    if isinstance(obj, dict):
        return {k: _normalize_email_context(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_email_context(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_normalize_email_context(v) for v in obj)
    return obj


def _render(template_string: str, context: dict) -> str:
    return Template(template_string).render(Context(context))


def get_email_provider() -> BaseEmailProvider:
    if getattr(settings, "TESTING", False):
        return DjangoCoreMailProvider()
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
    ctx = _normalize_email_context(copy.deepcopy(context) if context else {})
    try:
        template = EmailTemplate.objects.get(type=email_type, is_active=True)
    except EmailTemplate.DoesNotExist:
        default = DEFAULT_EMAIL_TEMPLATES.get(email_type)
        if not default:
            raise
        template, _ = EmailTemplate.objects.get_or_create(
            type=email_type,
            defaults={
                "subject": default["subject"],
                "html_body": default["html_body"],
                "text_body": default["text_body"],
                "is_active": True,
            },
        )
        if not template.is_active:
            template.is_active = True
            template.save(update_fields=["is_active", "updated_at"])

    subject = _render(template.subject, ctx)
    html = _render(template.html_body, ctx)
    text = _render(template.text_body, ctx) if (template.text_body or "").strip() else None

    mailer = provider or get_email_provider()
    provider_name = getattr(mailer, "provider_key", "resend")

    log = EmailLog.objects.create(
        to_email=to_email,
        type=email_type,
        status=EmailLog.Status.PENDING,
        provider=provider_name,
        metadata=ctx,
    )
    try:
        mailer.send(email_type, to_email, subject, html, text, from_email=from_email)
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
