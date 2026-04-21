"""Remove expired order export files (Celery Beat)."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from config.celery import app

from .models import OrderExportJob

logger = logging.getLogger(__name__)


@app.task(
    name="engine.apps.orders.cleanup_expired_order_exports",
    soft_time_limit=300,
    time_limit=330,
)
def cleanup_expired_order_exports() -> int:
    """Delete storage objects for completed exports past ``expires_at``; mark jobs EXPIRED."""
    run_stale_processing_fail()
    now = timezone.now()
    qs = OrderExportJob.objects.filter(
        status=OrderExportJob.Status.COMPLETED,
        expires_at__isnull=False,
        expires_at__lt=now,
    ).only("id", "file_path")

    count = 0
    for job in qs.iterator(chunk_size=200):
        path = (job.file_path or "").strip()
        if path:
            try:
                default_storage.delete(path)
            except Exception:
                logger.exception("export cleanup: delete failed job=%s path=%s", job.id, path)
        with transaction.atomic():
            OrderExportJob.objects.filter(pk=job.pk).update(
                status=OrderExportJob.Status.EXPIRED,
                file_path="",
                progress=0,
            )
        count += 1
    return count


def run_stale_processing_fail(*, older_than_minutes: int = 120) -> int:
    """Mark stuck PROCESSING jobs as FAILED (safety net)."""
    cutoff = timezone.now() - timedelta(minutes=older_than_minutes)
    return OrderExportJob.objects.filter(
        status=OrderExportJob.Status.PROCESSING,
        updated_at__lt=cutoff,
    ).update(
        status=OrderExportJob.Status.FAILED,
        error_message="Export timed out or worker was interrupted.",
        progress=0,
    )
