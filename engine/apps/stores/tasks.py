from __future__ import annotations

from datetime import timedelta

from config.celery import app
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from engine.apps.basic_analytics.models import StoreDashboardStatsSnapshot
from engine.apps.banners.models import Banner
from engine.apps.customers.models import Customer
from engine.apps.orders.models import Order
from engine.apps.products.models import Category, Product, ProductImage
from engine.apps.support.models import SupportTicketAttachment
from engine.apps.stores.lifecycle_emails import owner_email_only, queue_store_permanently_deleted
from engine.apps.stores.audit import write_store_lifecycle_audit
from engine.apps.stores.models import Store, StoreDeletionJob
from engine.apps.stores.services import get_store_owner_user
from engine.apps.stores.deletion_validation import (
    STORE_EMAIL_REQUIRED_FOR_DELETION_MESSAGE,
    require_store_contact_email_for_deletion,
)
from engine.apps.stores.store_lifecycle import (
    INACTIVITY_DAYS,
    LEASE_TIMEOUT_MINUTES,
    apply_inactivity_pending_delete,
)
from engine.core.admin_dashboard_cache import invalidate_notifications_and_dashboard_caches
from engine.core.media_deletion_service import schedule_media_deletion_from_keys
from engine.core.tenant_execution import system_scope


def _collect_non_empty(values) -> list[str]:
    return list(dict.fromkeys([str(v).strip() for v in values if str(v).strip()]))


def _purge_store_graph(store: Store, job: StoreDeletionJob | None) -> tuple[str, list[str]]:
    """
    Delete all store data; returns (store_name, emails) for post-delete notification.
    """
    store_public_id = store.public_id
    snapshot_name = store.name
    owner = owner_email_only(store)
    snapshot_emails = [owner] if owner else []

    invalidate_notifications_and_dashboard_caches(store_public_id)

    Order.objects.filter(store_id=store.id).delete()

    if job:
        job.current_step = StoreDeletionJob.STEP_CLEARING_CUSTOMERS
        job.save(update_fields=["current_step"])

    Customer.objects.filter(store_id=store.id).delete()

    if job:
        job.current_step = StoreDeletionJob.STEP_DELETING_PRODUCTS
        job.save(update_fields=["current_step"])

    product_image_names = _collect_non_empty(
        Product.objects.filter(store_id=store.id).values_list("image", flat=True)
    )
    gallery_image_names = _collect_non_empty(
        ProductImage.objects.filter(product__store_id=store.id).values_list("image", flat=True)
    )
    media_keys: list[str] = list(dict.fromkeys([*product_image_names, *gallery_image_names]))

    Product.objects.filter(store_id=store.id).delete()

    if job:
        job.current_step = StoreDeletionJob.STEP_DELETING_ANALYTICS
        job.save(update_fields=["current_step"])

    StoreDashboardStatsSnapshot.objects.filter(store_id=store.id).delete()

    if job:
        job.current_step = StoreDeletionJob.STEP_FINALIZING
        job.save(update_fields=["current_step"])

    media_keys.extend(store.get_media_keys())

    category_image_names = _collect_non_empty(
        Category.objects.filter(store_id=store.id).values_list("image", flat=True)
    )
    media_keys.extend(category_image_names)

    banner_image_names = _collect_non_empty(
        Banner.objects.filter(store_id=store.id).values_list("image", flat=True)
    )
    media_keys.extend(banner_image_names)

    attachment_file_names = _collect_non_empty(
        SupportTicketAttachment.objects.filter(ticket__store_id=store.id).values_list("file", flat=True)
    )
    media_keys.extend(attachment_file_names)
    media_keys = list(dict.fromkeys(media_keys))

    invalidate_notifications_and_dashboard_caches(store_public_id)
    with transaction.atomic():
        Store.objects.filter(id=store.id).delete()

    schedule_media_deletion_from_keys(media_keys)
    return snapshot_name, snapshot_emails


@app.task(name="engine.apps.stores.hard_delete_store")
def hard_delete_store(job_public_id: str, force: bool = False, reason: str | None = None) -> None:
    """
    Irreversibly delete a store and its data while updating job progress.

    Idempotent: if the store no longer exists, the job will be marked SUCCESS.
    """

    try:
        with system_scope(reason="hard_delete_store_task"):
            with transaction.atomic():
                job = StoreDeletionJob.objects.select_for_update().filter(public_id=job_public_id).first()
                if not job:
                    return

                # Idempotency: if already completed, do nothing.
                if job.status == StoreDeletionJob.Status.SUCCESS:
                    return
                now = timezone.now()

                # Lease-aware guard: if the job is RUNNING and the lease is still
                # valid another worker owns it.  If the lease expired the worker
                # crashed — transition to FAILED so the scheduler can re-enqueue.
                if job.status == StoreDeletionJob.Status.RUNNING:
                    lease_deadline = (
                        job.started_at + timedelta(minutes=LEASE_TIMEOUT_MINUTES)
                        if job.started_at
                        else None
                    )
                    if lease_deadline and lease_deadline > now:
                        return
                    job.status = StoreDeletionJob.Status.FAILED
                    job.error_message = "Lease expired (stale RUNNING); will be retried."
                    job.started_at = None
                    job.celery_task_id = ""
                    job.save(update_fields=["status", "error_message", "started_at", "celery_task_id"])
                    write_store_lifecycle_audit(
                        user=getattr(job, "user", None),
                        store=None,
                        store_public_id=job.store_public_id_snapshot,
                        action="STORE_DELETE_FAILED",
                        metadata={
                            "job_public_id": job.public_id,
                            "store_id_snapshot": job.store_id_snapshot,
                            "reason": "lease_expired",
                        },
                    )
                    return

                store = (
                    Store.objects.select_for_update()
                    .filter(id=job.store_id_snapshot)
                    .first()
                )

                if not store:
                    job.status = StoreDeletionJob.Status.SUCCESS
                    job.current_step = ""
                    job.error_message = ""
                    job.save(update_fields=["status", "current_step", "error_message"])
                    write_store_lifecycle_audit(
                        user=getattr(job, "user", None),
                        store=None,
                        store_public_id=job.store_public_id_snapshot,
                        action="STORE_DELETE_ALREADY_MISSING",
                        metadata={
                            "job_public_id": job.public_id,
                            "store_public_id": job.store_public_id_snapshot,
                            "store_id_snapshot": job.store_id_snapshot,
                            "reason": "store not found during execution",
                        },
                    )
                    return

                if store.status not in (Store.Status.INACTIVE, Store.Status.PENDING_DELETE):
                    job.status = StoreDeletionJob.Status.FAILED
                    job.celery_task_id = ""
                    job.error_message = (
                        f"Store status is {store.status}; expected INACTIVE or PENDING_DELETE."
                    )
                    job.save(update_fields=["status", "celery_task_id", "error_message"])
                    write_store_lifecycle_audit(
                        user=getattr(job, "user", None),
                        store=store,
                        action="STORE_DELETE_FAILED",
                        metadata={
                            "job_public_id": job.public_id,
                            "store_id_snapshot": job.store_id_snapshot,
                            "lifecycle_version": store.lifecycle_version,
                            "lifecycle_version_snapshot": job.lifecycle_version_snapshot,
                            "reason": "invalid_store_status",
                        },
                    )
                    return

                if store.lifecycle_version != job.lifecycle_version_snapshot:
                    job.status = StoreDeletionJob.Status.FAILED
                    job.celery_task_id = ""
                    job.error_message = (
                        f"lifecycle_version mismatch: store={store.lifecycle_version}, "
                        f"job={job.lifecycle_version_snapshot}. Store was likely restored."
                    )
                    job.save(update_fields=["status", "celery_task_id", "error_message"])
                    write_store_lifecycle_audit(
                        user=getattr(job, "user", None),
                        store=store,
                        action="STORE_DELETE_FAILED",
                        metadata={
                            "job_public_id": job.public_id,
                            "store_id_snapshot": job.store_id_snapshot,
                            "lifecycle_version": store.lifecycle_version,
                            "lifecycle_version_snapshot": job.lifecycle_version_snapshot,
                            "reason": "lifecycle_version_mismatch",
                        },
                    )
                    return

                if store.delete_at and store.delete_at > now and not force:
                    job.status = StoreDeletionJob.Status.SKIPPED_NOT_DUE
                    job.celery_task_id = ""
                    job.current_step = ""
                    job.error_message = ""
                    job.save(update_fields=["status", "celery_task_id", "current_step", "error_message"])
                    write_store_lifecycle_audit(
                        user=getattr(job, "user", None),
                        store=store,
                        action="STORE_DELETE_SKIPPED_NOT_DUE",
                        metadata={
                            "job_public_id": job.public_id,
                            "store_id_snapshot": job.store_id_snapshot,
                            "lifecycle_version": store.lifecycle_version,
                            "lifecycle_version_snapshot": job.lifecycle_version_snapshot,
                            "delete_at": store.delete_at.isoformat() if store.delete_at else None,
                        },
                    )
                    return

                try:
                    require_store_contact_email_for_deletion(store=store)
                except ValidationError:
                    job.status = StoreDeletionJob.Status.FAILED
                    job.celery_task_id = ""
                    job.current_step = StoreDeletionJob.STEP_FINALIZING
                    job.error_message = STORE_EMAIL_REQUIRED_FOR_DELETION_MESSAGE
                    job.save(update_fields=["status", "celery_task_id", "current_step", "error_message"])
                    write_store_lifecycle_audit(
                        user=getattr(job, "user", None),
                        store=store,
                        action="STORE_DELETE_FAILED",
                        metadata={
                            "job_public_id": job.public_id,
                            "store_id_snapshot": job.store_id_snapshot,
                            "lifecycle_version": store.lifecycle_version,
                            "lifecycle_version_snapshot": job.lifecycle_version_snapshot,
                            "reason": "missing_contact_email",
                        },
                    )
                    return

                if force:
                    why = (reason or "").strip() or "unspecified"
                    write_store_lifecycle_audit(
                        user=getattr(job, "user", None),
                        store=store,
                        action="STORE_DELETE_FORCED",
                        metadata={
                            "job_public_id": job.public_id,
                            "store_id_snapshot": job.store_id_snapshot,
                            "lifecycle_version": store.lifecycle_version,
                            "lifecycle_version_snapshot": job.lifecycle_version_snapshot,
                            "reason": why,
                        },
                    )

                job.status = StoreDeletionJob.Status.RUNNING
                job.started_at = now
                job.current_step = StoreDeletionJob.STEP_REMOVING_ORDERS
                job.error_message = ""
                job.save(update_fields=["status", "started_at", "current_step", "error_message"])
                write_store_lifecycle_audit(
                    user=getattr(job, "user", None),
                    store=store,
                    action="STORE_DELETE_STARTED",
                    metadata={
                        "job_public_id": job.public_id,
                        "store_id_snapshot": job.store_id_snapshot,
                        "lifecycle_version": store.lifecycle_version,
                        "lifecycle_version_snapshot": job.lifecycle_version_snapshot,
                        "force": bool(force),
                    },
                )

            # Perform destructive work outside the row-locking transaction, but only after the
            # decision has been made under DB locks.
            name, emails = _purge_store_graph(store, job)
            queue_store_permanently_deleted(name, emails)

            job.status = StoreDeletionJob.Status.SUCCESS
            job.current_step = ""
            job.error_message = ""
            job.save(update_fields=["status", "current_step", "error_message"])
            write_store_lifecycle_audit(
                user=getattr(job, "user", None),
                store=None,
                store_public_id=job.store_public_id_snapshot,
                action="STORE_DELETE_SUCCESS",
                metadata={
                    "job_public_id": job.public_id,
                    "store_id_snapshot": job.store_id_snapshot,
                    "lifecycle_version_snapshot": job.lifecycle_version_snapshot,
                },
            )
    except Exception as exc:
        job = StoreDeletionJob.objects.filter(public_id=job_public_id).first()
        if job:
            job.status = StoreDeletionJob.Status.FAILED
            job.celery_task_id = ""
            job.current_step = StoreDeletionJob.STEP_FINALIZING
            job.error_message = str(exc)
            job.save(update_fields=["status", "celery_task_id", "current_step", "error_message"])
            write_store_lifecycle_audit(
                user=getattr(job, "user", None),
                store=None,
                store_public_id=job.store_public_id_snapshot,
                action="STORE_DELETE_FAILED",
                metadata={
                    "job_public_id": job.public_id,
                    "store_id_snapshot": job.store_id_snapshot,
                    "lifecycle_version_snapshot": job.lifecycle_version_snapshot,
                    "error": str(exc),
                },
            )


@app.task(name="engine.apps.stores.process_store_lifecycle")
def process_store_lifecycle() -> None:
    """Celery Beat: due deletions, inactivity queue, reminder emails."""
    with system_scope(reason="process_store_lifecycle"):
        _process_due_store_deletions()
        _scan_inactivity_stores()
        _send_lifecycle_reminder_emails()


def _process_due_store_deletions() -> None:
    now = timezone.now()
    qs = Store.objects.filter(
        status__in=[Store.Status.INACTIVE, Store.Status.PENDING_DELETE],
        delete_at__lte=now,
    )
    for store in qs:
        # SKIPPED_NOT_DUE and FAILED are eligible for re-dispatch, but only because
        # the store-level delete_at__lte=now filter above already enforces the time
        # constraint.  SKIPPED_NOT_DUE is NOT a generic retry flag.
        job = (
            StoreDeletionJob.objects.filter(
                store_id_snapshot=store.id,
                status__in=[
                    StoreDeletionJob.Status.PENDING,
                    StoreDeletionJob.Status.SKIPPED_NOT_DUE,
                    StoreDeletionJob.Status.FAILED,
                ],
            )
            .order_by("-created_at")
            .first()
        )
        owner_user = get_store_owner_user(store)
        if not job:
            if not owner_user:
                continue
            job = StoreDeletionJob.objects.create(
                user=owner_user,
                store_public_id_snapshot=store.public_id,
                store_id_snapshot=store.id,
                delete_at_snapshot=store.delete_at,
                lifecycle_version_snapshot=store.lifecycle_version,
                status=StoreDeletionJob.Status.PENDING,
                current_step=StoreDeletionJob.STEP_REMOVING_ORDERS,
            )
        if job.celery_task_id:
            continue
        res = hard_delete_store.delay(job.public_id)
        job.celery_task_id = res.id
        job.save(update_fields=["celery_task_id"])


def _scan_inactivity_stores() -> None:
    from django.db.models import Q

    from engine.apps.stores.lifecycle_emails import queue_delete_scheduled
    from engine.apps.stores.models import StoreLifecycleAuditLog
    from engine.apps.stores.audit import write_store_lifecycle_audit

    now = timezone.now()
    threshold = now - timedelta(days=INACTIVITY_DAYS)
    qs = Store.objects.filter(
        status=Store.Status.ACTIVE,
    ).filter(
        Q(last_activity_at__isnull=True) | Q(last_activity_at__lt=threshold),
    )
    for store in qs:
        apply_inactivity_pending_delete(store)
        write_store_lifecycle_audit(
            user=None,
            store=store,
            action=StoreLifecycleAuditLog.Action.STORE_INACTIVITY_PENDING,
        )
        queue_delete_scheduled(store, from_inactivity=True)


def _send_lifecycle_reminder_emails() -> None:
    from engine.apps.stores.lifecycle_emails import (
        queue_inactive_recovery_reminder,
        queue_pending_delete_1d,
        queue_pending_delete_2d,
    )

    now = timezone.now()
    for store in Store.objects.filter(status=Store.Status.INACTIVE, delete_at__gt=now):
        if store.inactive_recovery_reminder_sent_at is not None:
            continue
        if store.delete_at is None or store.removed_at is None:
            continue
        reminder_at = store.delete_at - timedelta(days=7)
        if now >= reminder_at:
            queue_inactive_recovery_reminder(store)
            store.inactive_recovery_reminder_sent_at = now
            store.save(update_fields=["inactive_recovery_reminder_sent_at"])

    for store in Store.objects.filter(status=Store.Status.PENDING_DELETE, delete_at__gt=now):
        if store.pending_delete_2d_reminder_sent_at is None:
            if now >= store.delete_at - timedelta(days=2):
                queue_pending_delete_2d(store)
                store.pending_delete_2d_reminder_sent_at = now
                store.save(update_fields=["pending_delete_2d_reminder_sent_at"])

        if store.pending_delete_1d_reminder_sent_at is None:
            if now >= store.delete_at - timedelta(days=1):
                queue_pending_delete_1d(store)
                store.pending_delete_1d_reminder_sent_at = now
                store.save(update_fields=["pending_delete_1d_reminder_sent_at"])
