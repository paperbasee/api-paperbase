"""Lightweight store activity tracking (ACTIVE tenants only)."""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone

from engine.apps.stores.models import Store


def touch_store_activity(store: Store) -> None:
    """Update last_activity_at for ACTIVE stores (throttled)."""
    if store.status != Store.Status.ACTIVE:
        return
    interval = int(getattr(settings, "STORE_ACTIVITY_TOUCH_INTERVAL_SECONDS", 60))
    now = timezone.now()
    last = store.last_activity_at
    if last is not None and interval > 0 and (now - last).total_seconds() < interval:
        return
    Store.objects.filter(pk=store.pk, status=Store.Status.ACTIVE).update(last_activity_at=now)
