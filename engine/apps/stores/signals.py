"""Store signals."""

from django.core.exceptions import ValidationError
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from .models import Store
from .services import sync_store_owner_to_user


def _require_store_contact_email_before_delete(store: Store) -> None:
    contact = (getattr(store, "contact_email", "") or "").strip()
    if not contact:
        raise ValidationError("Add a store contact email before deleting this store.")


@receiver(post_save, sender=Store)
def sync_store_owner_on_save(sender, instance, **kwargs):
    """When Store owner_name changes, sync to the owner User."""
    sync_store_owner_to_user(instance)


@receiver(pre_delete, sender=Store)
def block_store_delete_without_contact_email(sender, instance: Store, **kwargs):
    _require_store_contact_email_before_delete(instance)
