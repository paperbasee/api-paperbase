from __future__ import annotations

import copy
import logging

from django.template import Context, Template
from django.utils import timezone

from .models import EmailLog, EmailTemplate
from .providers.base import BaseEmailProvider
from .providers.resend import ResendEmailProvider

_ERROR_MAX_LEN = 8000
logger = logging.getLogger(__name__)


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
    logger.info("EMAIL_SERVICE_ENTERED type=%s to=%s", email_type, to_email)
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
    logger.info("EMAIL_LOG_CREATED type=%s to=%s log_id=%s", email_type, to_email, log.id)

    mailer = provider or get_email_provider()
    try:
        logger.info("EMAIL_PROVIDER_SEND_START type=%s to=%s provider=%s", email_type, to_email, log.provider)
        mailer.send(to_email, subject, html, text, from_email=from_email)
    except Exception as exc:  # noqa: BLE001 — record any failure on the log
        err = str(exc)[:_ERROR_MAX_LEN]
        log.status = EmailLog.Status.FAILED
        log.error_message = err
        log.save(update_fields=["status", "error_message"])
        logger.exception("EMAIL_PROVIDER_SEND_FAILED type=%s to=%s log_id=%s", email_type, to_email, log.id)
        raise

    log.status = EmailLog.Status.SENT
    log.sent_at = timezone.now()
    log.error_message = ""
    log.save(update_fields=["status", "sent_at", "error_message"])
    logger.info("EMAIL_PROVIDER_SEND_SUCCESS type=%s to=%s log_id=%s", email_type, to_email, log.id)
    return log
