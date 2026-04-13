"""Store signals."""

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from .models import Store
from .deletion_validation import require_store_contact_email_for_deletion
from .services import sync_store_owner_to_user


@receiver(post_save, sender=Store)
def sync_store_owner_on_save(sender, instance, **kwargs):
    """When Store owner_name changes, sync to the owner User."""
    sync_store_owner_to_user(instance)


@receiver(pre_delete, sender=Store)
def block_store_delete_without_contact_email(sender, instance: Store, **kwargs):
    require_store_contact_email_for_deletion(store=instance)
