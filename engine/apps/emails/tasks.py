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
    task_id = getattr(getattr(send_email_task, "request", None), "id", None)
    logger.info(
        "EMAIL_TASK_START task_id=%s type=%s to=%s",
        task_id,
        email_type,
        to_email,
    )
    try:
        send_email(email_type, to_email, context or {}, from_email=from_email)
    except Exception:
        logger.exception(
            "EMAIL_TASK_FAILED task_id=%s type=%s to=%s",
            task_id,
            email_type,
            to_email,
        )
        raise
    logger.info(
        "EMAIL_TASK_SUCCESS task_id=%s type=%s to=%s",
        task_id,
        email_type,
        to_email,
    )
