from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from engine.core.admin_dashboard_cache import invalidate_notifications_and_dashboard_caches

from .models import SupportTicket


@receiver(post_save, sender=SupportTicket)
def support_ticket_invalidate_caches(sender, instance, **kwargs):
    invalidate_notifications_and_dashboard_caches(instance.store.public_id)


@receiver(post_delete, sender=SupportTicket)
def support_ticket_delete_invalidate_caches(sender, instance, **kwargs):
    invalidate_notifications_and_dashboard_caches(instance.store.public_id)
