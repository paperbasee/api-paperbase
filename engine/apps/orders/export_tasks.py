"""CSV generation for ``OrderExportJob`` (called from Celery)."""

from __future__ import annotations

import csv
import logging
import os
import tempfile
import uuid
from datetime import timedelta
from typing import Iterable

from django.core.files import File
from django.core.files.storage import default_storage
from django.db.models import Prefetch
from django.utils import timezone

from config.celery import app
from engine.core.media_upload_paths import generate_order_export_file_path
from engine.core.tenant_execution import system_scope

from .export_csv_format import ORDER_CSV_HEADERS, format_order_for_csv
from .export_queryset import build_export_queryset
from .models import Order, OrderAddress, OrderExportJob, OrderItem

logger = logging.getLogger(__name__)


def _chunks(ids: list[uuid.UUID], size: int) -> Iterable[list[uuid.UUID]]:
    for i in range(0, len(ids), size):
        yield ids[i : i + size]


@app.task(name="engine.apps.orders.export_orders_csv")
def export_orders_csv(job_id: str) -> None:
    run_order_export_csv_job(job_id)


def run_order_export_csv_job(job_id: str) -> None:
    try:
        job_uuid = uuid.UUID(str(job_id))
    except (ValueError, TypeError):
        logger.warning("order export: invalid job id %r", job_id)
        return

    with system_scope(reason="order_export_csv"):
        updated = OrderExportJob.objects.filter(
            id=job_uuid,
            status=OrderExportJob.Status.PENDING,
        ).update(status=OrderExportJob.Status.PROCESSING)
        if not updated:
            return

        job = OrderExportJob.objects.select_related("store").filter(id=job_uuid).first()
        if job is None:
            return

        store_id = job.store_id
        prefetch_items = Prefetch(
            "items",
            queryset=OrderItem.objects.order_by("id").select_related("product", "variant"),
        )
        prefetch_shipping_addresses = Prefetch(
            "addresses",
            queryset=OrderAddress.objects.filter(
                address_type=OrderAddress.AddressType.SHIPPING
            ),
        )

        if job.select_all:
            base_qs = build_export_queryset(store_id=store_id, filters=job.filters or {})
        else:
            base_qs = Order.objects.filter(
                store_id=store_id,
                public_id__in=(job.selected_order_ids or []),
            ).order_by("-created_at", "id")

        total = base_qs.count()
        id_list = list(base_qs.values_list("id", flat=True))

        tmp = None
        tmp_path = ""
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w+", newline="", encoding="utf-8", delete=False, suffix=".csv"
            )
            tmp_path = tmp.name
            writer = csv.writer(tmp)
            writer.writerow(ORDER_CSV_HEADERS)

            processed = 0
            last_progress = -1

            def maybe_save_progress() -> None:
                nonlocal last_progress
                if total <= 0:
                    pct = 100
                else:
                    pct = min(100, int(processed * 100 / total))
                if pct != last_progress and (
                    pct == 100 or pct - last_progress >= 5 or (processed > 0 and processed % 100 == 0)
                ):
                    last_progress = pct
                    OrderExportJob.objects.filter(id=job_uuid).update(progress=pct)

            if total == 0:
                maybe_save_progress()
            else:
                for chunk in _chunks(id_list, 500):
                    chunk_qs = (
                        Order.objects.filter(id__in=chunk, store_id=store_id)
                        .select_related("customer", "user")
                        .prefetch_related(prefetch_items, prefetch_shipping_addresses)
                        .order_by("-created_at", "id")
                    )
                    for order in chunk_qs:
                        writer.writerow(format_order_for_csv(order))
                        processed += 1
                        maybe_save_progress()

            tmp.flush()
            tmp.close()
            tmp = None

            export_date = timezone.now().date()
            object_name = generate_order_export_file_path(
                job.store.public_id, export_date, job_uuid
            )
            with open(tmp_path, "rb") as fh:
                default_storage.save(object_name, File(fh))

            OrderExportJob.objects.filter(id=job_uuid).update(
                status=OrderExportJob.Status.COMPLETED,
                file_path=object_name,
                progress=100,
                expires_at=timezone.now() + timedelta(hours=1),
                error_message="",
            )
        except Exception as exc:
            logger.exception("order export failed job=%s", job_id)
            OrderExportJob.objects.filter(id=job_uuid).update(
                status=OrderExportJob.Status.FAILED,
                error_message=str(exc)[:2000],
                progress=0,
            )
        finally:
            if tmp is not None:
                try:
                    tmp.close()
                except Exception:
                    pass
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.warning("order export: could not remove temp %s", tmp_path)
