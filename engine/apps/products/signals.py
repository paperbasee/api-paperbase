from django.db.models.signals import post_save
from django.dispatch import receiver

from engine.core.realtime import emit_store_events

from .models import Product


@receiver(post_save, sender=Product)
def product_realtime_events(sender, instance, created, **kwargs):
    events = ["product_updated", "product.created"] if created else ["product_updated", "product.updated"]
    emit_store_events(
        instance.store.public_id,
        events,
        {"product_public_id": instance.public_id},
    )
