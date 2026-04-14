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

_REDACT_PLACEHOLDER = "[REDACTED]"

# Keys (matched case-insensitively) whose values must not be persisted on EmailLog.metadata.
_METADATA_REDACT_KEYS = frozenset(
    {
        "phone",
        "address",
        "shipping_address",
        "line1",
        "line2",
        "postal_code",
        "zip",
        "customer_note",
        "order_summary",
    }
)

# Key names that may hold raw HTML; never persist.
_METADATA_HTML_KEYS = frozenset({"html_body", "html", "htmlbody"})


def resolve_email_log_store(context: dict | None):
    """
    Best-effort store for EmailLog FK from template context (before or after normalization).
    Never raises.
    """
    if not context:
        return None
    from engine.apps.orders.models import Order
    from engine.apps.stores.models import Store

    raw = context
    inst = raw.get("store")
    if isinstance(inst, Store):
        return inst

    order = raw.get("order")
    if isinstance(order, Order):
        return order.store

    sid = raw.get("store_id")
    if sid is not None:
        try:
            pk = int(sid)
        except (TypeError, ValueError):
            pk = None
        if pk is not None:
            found = Store.objects.filter(pk=pk).first()
            if found:
                return found

    spid = raw.get("store_public_id")
    if spid is not None and str(spid).strip():
        found = Store.objects.filter(public_id=str(spid).strip()).first()
        if found:
            return found

    return None


def _key_should_redact(key: str) -> bool:
    lk = key.lower()
    if lk in _METADATA_HTML_KEYS:
        return True
    if lk in _METADATA_REDACT_KEYS:
        return True
    return False


def _value_should_redact_as_html(key: str, value) -> bool:
    if not isinstance(value, str):
        return False
    s = value.strip()
    if len(s) <= 20:
        return False
    if key.lower() in ("body", "message", "content") and s.lstrip().startswith("<"):
        return True
    return s.lstrip().startswith("<") and "<html" in s[:200].lower()


def sanitize_email_metadata_for_storage(obj):
    """
    Return a JSON-serializable structure safe to persist on EmailLog (redacts PII / HTML).
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _key_should_redact(k):
                out[k] = _REDACT_PLACEHOLDER
                continue
            if _value_should_redact_as_html(k, v):
                out[k] = _REDACT_PLACEHOLDER
                continue
            out[k] = sanitize_email_metadata_for_storage(v)
        return out
    if isinstance(obj, list):
        return [sanitize_email_metadata_for_storage(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize_email_metadata_for_storage(v) for v in obj)
    return obj


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
    raw_context = copy.deepcopy(context) if context else {}
    log_store = resolve_email_log_store(raw_context if isinstance(raw_context, dict) else None)
    ctx = _normalize_email_context(copy.deepcopy(raw_context) if raw_context else {})
    metadata_for_storage = sanitize_email_metadata_for_storage(ctx)
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
        store=log_store,
        metadata=metadata_for_storage,
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
