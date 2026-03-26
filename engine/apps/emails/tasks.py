from __future__ import annotations

import logging

from config.celery import app

from .services import send_email

logger = logging.getLogger(__name__)


@app.task(name="engine.apps.emails.send_email")
def send_email_task(
    email_type: str,
    to_email: str,
    context: dict | None = None,
    from_email: str | None = None,
):
    logger.info("EMAIL_TASK_STARTED type=%s to=%s", email_type, to_email)
    try:
        send_email(email_type, to_email, context or {}, from_email=from_email)
    except Exception:
        logger.exception("EMAIL_TASK_FAILED type=%s to=%s", email_type, to_email)
        raise
    logger.info("EMAIL_TASK_COMPLETED type=%s to=%s", email_type, to_email)
