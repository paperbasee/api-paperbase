"""
Marketing event dispatcher.

Resolves the active store from the request, looks up enabled marketing
integrations, checks per-event toggles, and delegates to provider-specific
service modules.  All exceptions are caught so callers are never broken.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_store(request, obj=None):
    """Return the Store for a request, falling back to obj.store."""
    from engine.core.tenancy import get_active_store

    ctx = get_active_store(request)
    if ctx.store:
        return ctx.store
    if obj and hasattr(obj, "store"):
        return obj.store
    if obj and hasattr(obj, "store_id"):
        from engine.apps.stores.models import Store
        try:
            return Store.objects.get(pk=obj.store_id)
        except Store.DoesNotExist:
            pass
    return None


def _get_integrations(store):
    """Fetch active marketing integrations with event settings for a store."""
    from engine.apps.marketing_integrations.models import MarketingIntegration

    return (
        MarketingIntegration.objects
        .filter(store=store, is_active=True)
        .select_related("event_settings")
    )


def _dispatch(request, event_flag: str, handler_name: str, *args: Any) -> None:
    """
    Core dispatch loop.

    Args:
        request: The incoming HTTP request.
        event_flag: Attribute name on IntegrationEventSettings (e.g. "track_purchase").
        handler_name: Function name in the provider service module.
        *args: Extra args forwarded to the handler after (request, ..., integration).
    """
    from engine.apps.marketing_integrations.services import facebook_service

    store = _resolve_store(request, args[0] if args else None)
    if not store:
        return

    integrations = _get_integrations(store)

    provider_modules = {
        "facebook": facebook_service,
    }

    for integration in integrations:
        try:
            settings = getattr(integration, "event_settings", None)
            if settings and not getattr(settings, event_flag, False):
                continue

            module = provider_modules.get(integration.provider)
            if module is None:
                continue

            fn = getattr(module, handler_name, None)
            if fn is None:
                continue

            fn(request, *args, integration)
        except Exception:
            logger.exception(
                "Marketing event '%s' failed for integration %s.",
                handler_name,
                integration.public_id,
            )


def track_purchase(request, order) -> None:
    _dispatch(request, "track_purchase", "track_purchase", order)


def track_add_to_cart(request, product, quantity: int) -> None:
    _dispatch(request, "track_add_to_cart", "track_add_to_cart", product, quantity)


def track_initiate_checkout(request) -> None:
    _dispatch(request, "track_initiate_checkout", "track_initiate_checkout")


def track_view_content(request, product) -> None:
    _dispatch(request, "track_view_content", "track_view_content", product)


def track_add_to_wishlist(request, product) -> None:
    _dispatch(request, "track_view_content", "track_add_to_wishlist", product)


def track_search(request, query: str) -> None:
    _dispatch(request, "track_view_content", "track_search", query)


def track_contact(request) -> None:
    _dispatch(request, "track_purchase", "track_contact")


def track_add_payment_info(request, order_data: dict | None = None) -> None:
    pass
