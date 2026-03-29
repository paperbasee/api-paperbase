"""Shared courier resolution and dispatch for admin send-to-courier flows."""

from __future__ import annotations

import logging

import requests as http_requests
from rest_framework.exceptions import ValidationError

from engine.apps.couriers.models import Courier
from engine.apps.orders.models import Order

logger = logging.getLogger(__name__)


def resolve_courier(*, store, order: Order) -> Courier:
    """
    Pick the active Courier for this store.

    If order.courier_provider matches an active courier, use it.
    If exactly one active courier exists, use it.
    Otherwise raise ValidationError.
    """
    active = Courier.objects.filter(store=store, is_active=True).order_by("-updated_at", "-id")
    preferred = (order.courier_provider or "").strip()
    if preferred:
        match = active.filter(provider=preferred).first()
        if match:
            return match
    lst = list(active[:2])
    if len(lst) == 0:
        raise ValidationError({"detail": "No active courier configured for this store."})
    if len(lst) == 1:
        return lst[0]
    raise ValidationError(
        {
            "detail": (
                "Multiple active couriers for this store. Deactivate extras or set this order's "
                "courier_provider to match one of them before dispatch."
            )
        }
    )


def run_courier_api(order: Order, courier: Courier) -> dict:
    """Call Steadfast create_order; returns dict with consignment_id, raw_response."""
    if courier.provider != Courier.Provider.STEADFAST:
        raise ValidationError({"detail": f"Unsupported courier provider: {courier.provider}"})
    from engine.apps.couriers.services import steadfast_service as svc

    try:
        return svc.create_order(order, courier)
    except http_requests.HTTPError as exc:
        logger.exception("Courier API error for order %s", order.order_number)
        raise ValidationError(
            {"detail": f"Courier API error: {exc.response.text if exc.response else str(exc)}"}
        ) from exc
    except ValidationError:
        raise
    except Exception as exc:
        logger.exception("Unexpected courier error for order %s", order.order_number)
        raise ValidationError({"detail": f"Courier error: {str(exc)}"}) from exc


def persist_dispatch(order: Order, courier: Courier, consignment_id: str) -> None:
    order.courier_provider = courier.provider
    order.courier_consignment_id = consignment_id or ""
    order.sent_to_courier = True
    order.save(
        update_fields=[
            "courier_provider",
            "courier_consignment_id",
            "sent_to_courier",
            "updated_at",
        ]
    )
