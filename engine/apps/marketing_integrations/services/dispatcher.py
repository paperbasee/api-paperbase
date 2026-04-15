"""
Marketing event dispatcher.

Resolves the active store from explicit context, looks up enabled marketing
integrations, checks per-event toggles, and delegates to provider-specific
service modules. All exceptions are caught so callers are never broken.

Deterministic Meta ``event_id`` values are built in ``meta_event_ids`` (no random
UUID fallbacks). If an ID cannot be built (e.g. missing session), the event is
skipped and an error is logged.
"""

from __future__ import annotations

import logging
from typing import Any

from engine.apps.marketing_integrations import meta_event_ids
from engine.core.tenant_context import get_current_store
from engine.core.tenant_guard import TenantViolationError

logger = logging.getLogger(__name__)

# Must match BooleanField defaults on IntegrationEventSettings — getattr(..., False) was wrong
# for flags that default to True: a missing attribute would skip sending Purchase / InitiateCheckout.
_EVENT_FLAG_DEFAULTS: dict[str, bool] = {
    "track_purchase": True,
    "track_initiate_checkout": True,
    "track_view_content": False,
    "track_search": False,
}


def _should_skip_event_for_settings(settings, event_flag: str) -> bool:
    """Skip only when integration has settings and the flag is explicitly off."""
    if not settings:
        return False
    default = _EVENT_FLAG_DEFAULTS.get(event_flag, True)
    enabled = bool(getattr(settings, event_flag, default))
    return not enabled


def _resolve_store(*, store=None):
    """Return explicitly provided store or current request-scoped store."""
    return store or get_current_store()


def _get_integrations(store):
    """Fetch active marketing integrations with event settings for a store."""
    from engine.apps.marketing_integrations.models import MarketingIntegration

    return (
        MarketingIntegration.objects
        .filter(store=store, is_active=True)
        .select_related("event_settings")
    )


def _dispatch(request, event_flag: str, handler_name: str, *args: Any, store=None) -> None:
    """
    Core dispatch loop.

    Args:
        request: The incoming HTTP request.
        event_flag: Attribute name on IntegrationEventSettings (e.g. "track_purchase").
        handler_name: Function name in the provider service module.
        *args: Extra args forwarded to the handler after (request, ..., integration).
    """
    from engine.apps.marketing_integrations.services import facebook_service

    store = _resolve_store(store=store)
    if not store:
        raise TenantViolationError("Dispatcher requires explicit tenant context.")

    integrations = _get_integrations(store)

    provider_modules = {
        "facebook": facebook_service,
    }

    for integration in integrations:
        try:
            settings = getattr(integration, "event_settings", None)
            if _should_skip_event_for_settings(settings, event_flag):
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
    # Always pass the order's store: dashboard requests (esp. superusers) often have no
    # tenant in ContextVar (middleware clears it for platform scope), while storefront
    # InitiateCheckout still resolves via API key.
    try:
        eid = meta_event_ids.build_purchase_event_id(order)
    except ValueError as e:
        logger.error("Meta CAPI skip (purchase): %s", e)
        return
    store = getattr(order, "store", None)
    _dispatch(request, "track_purchase", "track_purchase", order, eid, store=store)


def track_initiate_checkout(request) -> None:
    eid = meta_event_ids.build_checkout_event_id(request)
    if not eid:
        logger.error(
            "Meta CAPI skip (initiate_checkout): no Django session key; cannot build deterministic event_id",
        )
        return
    _dispatch(request, "track_initiate_checkout", "track_initiate_checkout", eid)


def track_view_content(request, product) -> None:
    eid = meta_event_ids.build_view_content_event_id(product)
    if not eid:
        logger.error(
            "Meta CAPI skip (view_content): missing product.public_id",
        )
        return
    _dispatch(request, "track_view_content", "track_view_content", product, eid)


def track_search(request, query: str) -> None:
    eid = meta_event_ids.build_search_event_id(request, query)
    if not eid:
        logger.error(
            "Meta CAPI skip (search): no Django session key; cannot build deterministic event_id",
        )
        return
    _dispatch(request, "track_search", "track_search", query, eid)
