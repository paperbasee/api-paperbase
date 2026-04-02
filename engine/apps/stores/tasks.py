from __future__ import annotations

from config.celery import app
from django.db import transaction

from engine.apps.analytics.models import StoreAnalytics, StoreDashboardStatsSnapshot
from engine.apps.customers.models import Customer
from engine.apps.orders.models import Order
from engine.apps.products.models import Category, Product, ProductImage
from engine.apps.banners.models import Banner
from engine.apps.support.models import SupportTicketAttachment
from engine.core.media_deletion_service import schedule_media_deletion_from_keys
from engine.apps.stores.models import Store, StoreDeletionJob
from engine.core.admin_dashboard_cache import invalidate_notifications_and_dashboard_caches
from engine.core.tenant_execution import system_scope


def _collect_non_empty(values) -> list[str]:
    return list(dict.fromkeys([str(v).strip() for v in values if str(v).strip()]))


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
            product_image_names = _collect_non_empty(
                Product.objects.filter(store_id=store.id).values_list("image", flat=True)
            )
            gallery_image_names = _collect_non_empty(
                ProductImage.objects.filter(product__store_id=store.id).values_list(
                "image", flat=True
            )
            )
            media_keys: list[str] = list(dict.fromkeys([*product_image_names, *gallery_image_names]))

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
            media_keys.extend(store.get_media_keys())

            # Category images (store-scoped, but category records are still present until store delete cascade).
            category_image_names = _collect_non_empty(
                Category.objects.filter(store_id=store.id).values_list("image", flat=True)
            )
            media_keys.extend(category_image_names)

            # Banner images.
            banner_image_names = _collect_non_empty(
                Banner.objects.filter(store_id=store.id).values_list("image", flat=True)
            )
            media_keys.extend(banner_image_names)

            # Support ticket attachments.
            attachment_file_names = _collect_non_empty(
                SupportTicketAttachment.objects.filter(ticket__store_id=store.id).values_list("file", flat=True)
            )
            media_keys.extend(attachment_file_names)
            media_keys = list(dict.fromkeys(media_keys))

            # Final DB deletion: store + remaining store-scoped data via cascades.
            invalidate_notifications_and_dashboard_caches(store_public_id)
            with transaction.atomic():
                Store.objects.filter(id=store.id).delete()

            schedule_media_deletion_from_keys(media_keys)

            job.status = StoreDeletionJob.Status.SUCCESS
            job.current_step = ""
            job.error_message = ""
            job.save(update_fields=["status", "current_step", "error_message"])
    except Exception as exc:
        job.status = StoreDeletionJob.Status.FAILED
        job.current_step = StoreDeletionJob.STEP_FINALIZING
        job.error_message = str(exc)
        job.save(update_fields=["status", "current_step", "error_message"])

