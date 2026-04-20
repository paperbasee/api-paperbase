from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from engine.core.admin_dashboard_cache import (
    bump_dashboard_stats_cache_version,
    invalidate_dashboard_live_cache,
)

from .models import Customer


@receiver(post_save, sender=Customer)
def customer_invalidate_dashboard(sender, instance, **kwargs):
    invalidate_dashboard_live_cache(instance.store.public_id)
    bump_dashboard_stats_cache_version(instance.store.public_id)


@receiver(post_delete, sender=Customer)
def customer_delete_invalidate_dashboard(sender, instance, **kwargs):
    invalidate_dashboard_live_cache(instance.store.public_id)
    bump_dashboard_stats_cache_version(instance.store.public_id)
