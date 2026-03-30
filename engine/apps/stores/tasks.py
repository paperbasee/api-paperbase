from __future__ import annotations

from config.celery import app
from django.core.files.storage import default_storage
from django.db import transaction

from engine.apps.analytics.models import StoreAnalytics, StoreDashboardStatsSnapshot
from engine.apps.customers.models import Customer
from engine.apps.orders.models import Order
from engine.apps.products.models import Category, Product, ProductImage
from engine.apps.banners.models import Banner
from engine.apps.support.models import SupportTicketAttachment
from engine.apps.stores.models import Store, StoreDeletionJob
from engine.core.admin_dashboard_cache import invalidate_notifications_and_dashboard_caches
from engine.core.tenant_execution import system_scope


def _delete_storage_file(file_name: str | None) -> None:
    """
    Delete a file from the configured storage backend (filesystem/S3/etc).

    Django does not automatically delete files from storage on model hard-delete,
    so we explicitly remove media/assets as part of store deletion.
    """

    if not file_name:
        return
    try:
        default_storage.delete(file_name)
    except Exception:
        # Deleting media should not block DB deletion; we track failure via job.error_message later if needed.
        pass


def _delete_storage_files(file_names) -> None:
    for name in file_names:
        _delete_storage_file(name)


@app.task(name="engine.apps.stores.hard_delete_store")
def hard_delete_store(job_public_id: str) -> None:
    """
    Irreversibly delete a store and its data while updating job progress.

    Idempotent: if the store no longer exists, the job will be marked SUCCESS.

    Note: accepts job_public_id (e.g. dlj_xxx) — do NOT pass internal integer PKs.
    """

    job = StoreDeletionJob.objects.filter(public_id=job_public_id).first()
    if not job:
        return

    try:
        with system_scope(reason="hard_delete_store_task"):
            # Reload store by snapshot (store is hard-deleted at end of this function).
            store = Store.objects.filter(id=job.store_id_snapshot).first()
            if not store:
                job.status = StoreDeletionJob.Status.SUCCESS
                job.current_step = ""
                job.error_message = ""
                job.save(update_fields=["status", "current_step", "error_message"])
                return

            job.status = StoreDeletionJob.Status.RUNNING
            job.current_step = StoreDeletionJob.STEP_REMOVING_ORDERS
            job.error_message = ""
            job.save(update_fields=["status", "current_step", "error_message"])

            store_public_id = store.public_id
            # QuerySet.delete() does not emit per-row signals; clear summary/overview caches.
            invalidate_notifications_and_dashboard_caches(store_public_id)

            # Step 1: Remove orders (and their dependent rows).
            Order.objects.filter(store_id=store.id).delete()

            job.current_step = StoreDeletionJob.STEP_CLEARING_CUSTOMERS
            job.save(update_fields=["current_step"])

            # Step 2: Clear customer data.
            Customer.objects.filter(store_id=store.id).delete()

            job.current_step = StoreDeletionJob.STEP_DELETING_PRODUCTS
            job.save(update_fields=["current_step"])

            # Step 3: Delete product media + product graph (variants/images/etc).
            product_image_names = Product.objects.filter(store_id=store.id).values_list("image", flat=True)
            gallery_image_names = ProductImage.objects.filter(product__store_id=store.id).values_list(
                "image", flat=True
            )
            _delete_storage_files(product_image_names)
            _delete_storage_files(gallery_image_names)

            # Important ordering:
            # - orders are deleted before products to avoid PROTECT constraints
            #   (OrderItem.product is PROTECT).
            Product.objects.filter(store_id=store.id).delete()

            job.current_step = StoreDeletionJob.STEP_DELETING_ANALYTICS
            job.save(update_fields=["current_step"])

            # Step 4: Delete analytics.
            StoreAnalytics.objects.filter(store_id=store.id).delete()
            StoreDashboardStatsSnapshot.objects.filter(store_id=store.id).delete()

            job.current_step = StoreDeletionJob.STEP_FINALIZING
            job.save(update_fields=["current_step"])

            # Step 5: Delete remaining media/assets before hard-deleting the store.
            _delete_storage_file(getattr(store.logo, "name", None))

            # Category images (store-scoped, but category records are still present until store delete cascade).
            category_image_names = Category.objects.filter(store_id=store.id).values_list("image", flat=True)
            _delete_storage_files(category_image_names)

            # Banner images.
            banner_image_names = Banner.objects.filter(store_id=store.id).values_list("image", flat=True)
            _delete_storage_files(banner_image_names)

            # Support ticket attachments.
            attachment_file_names = SupportTicketAttachment.objects.filter(
                ticket__store_id=store.id
            ).values_list("file", flat=True)
            _delete_storage_files(attachment_file_names)

            # Final DB deletion: store + remaining store-scoped data via cascades.
            invalidate_notifications_and_dashboard_caches(store_public_id)
            with transaction.atomic():
                Store.objects.filter(id=store.id).delete()

            job.status = StoreDeletionJob.Status.SUCCESS
            job.current_step = ""
            job.error_message = ""
            job.save(update_fields=["status", "current_step", "error_message"])
    except Exception as exc:
        job.status = StoreDeletionJob.Status.FAILED
        job.current_step = StoreDeletionJob.STEP_FINALIZING
        job.error_message = str(exc)
        job.save(update_fields=["status", "current_step", "error_message"])

