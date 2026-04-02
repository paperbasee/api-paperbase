"""Inventory services: tenant-safe stock adjustment and audit logging."""
from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from engine.core.tenant_context import require_store_context
from .utils import clamp_stock

logger = logging.getLogger(__name__)


def _lock_inventory(*, store_id: int, product_id, variant_id: int | None):
    from .models import Inventory

    qs = Inventory.objects.select_for_update().select_related("product", "variant")
    if variant_id is None:
        return qs.get(
            product_id=product_id,
            variant__isnull=True,
            product__store_id=store_id,
        )
    return qs.get(
        product_id=product_id,
        variant_id=variant_id,
        product__store_id=store_id,
        variant__product_id=product_id,
    )


def adjust_inventory_stock(
    *,
    store_id: int,
    product_id,
    variant_id: int | None,
    delta_qty: int,
    reason: str,
    source: str,
    reference_id: str = "",
    reference: str = "",
    actor=None,
    allow_negative: bool = False,
):
    """
    Tenant-scoped stock mutation entrypoint.
    Positive delta reduces available inventory, negative delta restores it.
    """
    from .models import Inventory, StockMovement

    if delta_qty is None:
        raise ValidationError("delta_qty is required.")
    delta_qty = int(delta_qty)
    if delta_qty == 0:
        raise ValidationError("delta_qty must be non-zero.")

    with transaction.atomic():
        try:
            inventory = _lock_inventory(store_id=store_id, product_id=product_id, variant_id=variant_id)
        except Inventory.DoesNotExist as exc:
            raise ValidationError("Invalid product for this store.") from exc

        current_quantity = int(inventory.quantity)
        next_quantity = current_quantity - delta_qty
        clamped_next_quantity = clamp_stock(next_quantity)
        applied_change = clamped_next_quantity - current_quantity

        Inventory.objects.filter(pk=inventory.pk).update(quantity=clamped_next_quantity)
        inventory.refresh_from_db(fields=["quantity", "updated_at"])

        StockMovement.objects.create(
            inventory=inventory,
            change=applied_change,
            reason=reason,
            source=source,
            reference_id=(reference_id or "")[:100],
            reference=(reference or "")[:255],
            actor=actor,
        )
        if inventory.is_low_stock() and inventory.quantity <= inventory.low_stock_threshold:
            _create_low_stock_notification(inventory)

        def _enqueue_cache_sync() -> None:
            try:
                from .tasks import sync_product_stock_cache_for_store

                sync_product_stock_cache_for_store.delay(int(store_id))
            except Exception:
                logger.exception(
                    "Failed to enqueue product stock cache sync",
                    extra={"store_id": int(store_id)},
                )

        transaction.on_commit(_enqueue_cache_sync)
        return inventory


def adjust_stock(
    inventory,
    change,
    reason="adjustment",
    source="admin",
    reference_id="",
    reference="",
    actor=None,
):
    """
    Backward-compatible inventory-row adjust wrapper for admin adjust endpoint.
    """
    return adjust_inventory_stock(
        store_id=inventory.product.store_id,
        product_id=inventory.product_id,
        variant_id=inventory.variant_id,
        delta_qty=-int(change),
        reason=reason,
        source=source,
        reference_id=reference_id,
        reference=reference,
        actor=actor,
    )


def _create_low_stock_notification(inventory):
    """Create a tenant-scoped low-stock notification for a concrete recipient."""
    try:
        from engine.apps.accounts.models import User
        from engine.apps.notifications.models import StaffNotification
        from engine.apps.stores.models import StoreMembership

        store = require_store_context()
        if inventory.product.store_id != store.id:
            raise ValidationError("Inventory store does not match current tenant context.")

        recipient = (
            User.objects.filter(
                store_memberships__store=store,
                store_memberships__is_active=True,
                store_memberships__role__in=[
                    StoreMembership.Role.OWNER,
                    StoreMembership.Role.ADMIN,
                    StoreMembership.Role.STAFF,
                ],
            )
            .order_by("id")
            .first()
        )
        if recipient is None:
            return

        title = f"Low stock: {inventory.product.name}"
        if inventory.variant_id:
            title += f" ({inventory.variant.sku or f'Variant {inventory.variant_id}'})"
        StaffNotification.objects.create(
            store=store,
            user=recipient,
            message_type=StaffNotification.MessageType.LOW_STOCK,
            title=title,
            payload={
                'product_id': str(inventory.product_id),
                'variant_id': inventory.variant_id,
                'quantity': inventory.quantity,
                'threshold': inventory.low_stock_threshold,
            },
        )
    except Exception:
        pass  # Do not fail stock update if notification fails
