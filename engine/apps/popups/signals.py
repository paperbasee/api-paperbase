"""Popup cache invalidation signals."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from engine.core import cache_service

from .models import StorePopup, StorePopupImage


def _invalidate_popup_cache(store_public_id: str) -> None:
    cache_service.delete(f"cache:{store_public_id}:popup:active")


@receiver(post_save, sender=StorePopup)
def invalidate_popup_cache_on_save(sender, instance: StorePopup, **kwargs):
    _invalidate_popup_cache(instance.store.public_id)


@receiver(post_delete, sender=StorePopup)
def invalidate_popup_cache_on_delete(sender, instance: StorePopup, **kwargs):
    _invalidate_popup_cache(instance.store.public_id)


@receiver(post_save, sender=StorePopupImage)
def invalidate_popup_cache_on_image_save(sender, instance: StorePopupImage, **kwargs):
    _invalidate_popup_cache(instance.popup.store.public_id)


@receiver(post_delete, sender=StorePopupImage)
def invalidate_popup_cache_on_image_delete(sender, instance: StorePopupImage, **kwargs):
    _invalidate_popup_cache(instance.popup.store.public_id)
