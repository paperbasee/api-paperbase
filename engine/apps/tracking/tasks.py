from __future__ import annotations

import logging
import os
from django.utils import timezone

from config.celery import app

logger = logging.getLogger(__name__)

EVENT_LOG_RETENTION_HOURS = int(os.environ.get("EVENT_LOG_RETENTION_HOURS", "72"))

# Ensure Celery discovers tasks declared in tracking.flush_tasks / tiktok_flush_tasks.
from engine.apps.tracking import flush_tasks  # noqa: F401,E402
from engine.apps.tracking import tiktok_flush_tasks  # noqa: F401,E402


@app.task(
    name="engine.apps.tracking.cleanup_old_event_logs",
    soft_time_limit=120,
    time_limit=150,
)
def cleanup_old_event_logs() -> int:
    """Celery beat: delete StoreEventLog rows older than EVENT_LOG_RETENTION_HOURS (default 72; app=tracking only)."""
    from datetime import timedelta

    from engine.apps.marketing_integrations.models import StoreEventLog

    cutoff = timezone.now() - timedelta(hours=EVENT_LOG_RETENTION_HOURS)
    qs = StoreEventLog.objects.filter(created_at__lt=cutoff, app="tracking")
    deleted, _ = qs.delete()
    return int(deleted or 0)

