"""
Inventory services: stock adjustments and low-stock notifications.
"""
from django.db import transaction


def adjust_stock(inventory, change, reason='adjustment', reference='', actor=None):
    """
    Adjust inventory quantity and record a StockMovement.
    Optionally create a StaffInboxNotification when stock falls at or below low_stock_threshold.
    """
    from .models import Inventory, StockMovement

    with transaction.atomic():
        inventory.quantity = max(0, inventory.quantity + change)
        inventory.save(update_fields=['quantity', 'updated_at'])
        StockMovement.objects.create(
            inventory=inventory,
            change=change,
            reason=reason,
            reference=reference,
            actor=actor,
        )
        if inventory.is_low_stock() and inventory.quantity <= inventory.low_stock_threshold:
            _create_low_stock_notification(inventory)


def _create_low_stock_notification(inventory):
    """Create a system notification for low stock (visible to all staff when user=null)."""
    try:
        from engine.apps.notifications.models import StaffInboxNotification
        title = f"Low stock: {inventory.product.name}"
        if inventory.variant_id:
            title += f" ({inventory.variant.sku or f'Variant {inventory.variant_id}'})"
        StaffInboxNotification.objects.create(
            user=None,
            message_type=StaffInboxNotification.MessageType.LOW_STOCK,
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
