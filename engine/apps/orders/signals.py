from django.db.models.signals import post_save
from django.dispatch import receiver

from engine.core.realtime import emit_store_events

from .models import Order


@receiver(post_save, sender=Order)
def order_realtime_events(sender, instance, created, **kwargs):
    events = ["order_created", "order.created"] if created else ["order_updated", "order.updated"]
    emit_store_events(
        instance.store.public_id,
        events,
        {"order_public_id": instance.public_id},
    )
