"""Store signals."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Store
from .services import sync_store_owner_to_user


@receiver(post_save, sender=Store)
def sync_store_owner_on_save(sender, instance, **kwargs):
    """When Store owner_name changes, sync to the owner User."""
    sync_store_owner_to_user(instance)
