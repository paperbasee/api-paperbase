"""Store signals."""

from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from engine.core import cache_service

from .models import Store, StoreSettings
from .services import (
    invalidate_store_api_key_resolution_cache_from_digest,
    sync_store_owner_to_user,
)


def _require_store_contact_email_before_delete(store: Store) -> None:
    contact = (getattr(store, "contact_email", "") or "").strip()
    if not contact:
        raise ValidationError("Add a store contact email before deleting this store.")


@receiver(post_save, sender=Store)
def sync_store_owner_on_save(sender, instance, **kwargs):
    """When Store owner_name changes, sync to the owner User."""
    sync_store_owner_to_user(instance)
    if not instance.is_active:
        for digest in instance.api_keys.values_list("key_hash", flat=True):
            invalidate_store_api_key_resolution_cache_from_digest(digest)


@receiver(pre_delete, sender=Store)
def block_store_delete_without_contact_email(sender, instance: Store, **kwargs):
    _require_store_contact_email_before_delete(instance)


def _invalidate_store_public_cache(store_public_id: str) -> None:
    cache_service.delete(f"cache:{store_public_id}:store_public:v1")


@receiver(post_save, sender=StoreSettings)
def invalidate_store_public_cache_on_settings_save(sender, instance: StoreSettings, **kwargs):
    _invalidate_store_public_cache(instance.store.public_id)


@receiver(post_delete, sender=StoreSettings)
def invalidate_store_public_cache_on_settings_delete(sender, instance: StoreSettings, **kwargs):
    _invalidate_store_public_cache(instance.store.public_id)
