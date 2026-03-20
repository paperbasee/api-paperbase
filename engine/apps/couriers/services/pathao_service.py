"""
Pathao courier integration service.

Sends orders to Pathao via their Aladdin API and retrieves tracking status.
Decryption of stored credentials happens exclusively inside this module.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from engine.core.encryption import decrypt_value

logger = logging.getLogger(__name__)

PATHAO_ORDER_ENDPOINT = "/aladdin/api/v1/orders"
PATHAO_TRACKING_ENDPOINT = "/aladdin/api/v1/orders/{consignment_id}/tracking"


def _base_url(courier) -> str:
    api_key = decrypt_value(courier.api_key_encrypted)
    return api_key.rstrip("/") if api_key.startswith("http") else "https://hermes-api.p-stagearea.xyz"


def _auth_headers(courier) -> dict[str, str]:
    token = decrypt_value(courier.access_token_encrypted)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _build_payload(order) -> dict[str, Any]:
    """
    Build Pathao order payload from an Order instance.

    Pathao auto-detects location from the full address, so we intentionally
    exclude recipient_city, recipient_zone, and recipient_area.
    """
    items = list(order.items.select_related("product").all())
    item_description = ", ".join(
        f"{item.product.name} x{item.quantity}" for item in items
    )

    return {
        "store_id": None,
        "merchant_order_id": order.order_number,
        "recipient_name": order.shipping_name or "Customer",
        "recipient_phone": order.phone,
        "recipient_address": order.shipping_address,
        "amount_to_collect": float(order.total),
        "item_type": 2,  # Parcel
        "special_instruction": "",
        "item_quantity": sum(item.quantity for item in items),
        "item_weight": 0.5,
        "item_description": item_description[:255],
    }


def create_order(order, courier) -> dict[str, Any]:
    """
    Create an order on Pathao.

    Returns dict with keys: consignment_id, tracking_code, status, raw_response.
    Raises requests.HTTPError on failure.
    """
    base = _base_url(courier)
    url = f"{base}{PATHAO_ORDER_ENDPOINT}"
    payload = _build_payload(order)
    headers = _auth_headers(courier)

    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    result_data = data.get("data", data)
    return {
        "consignment_id": str(result_data.get("consignment_id", "")),
        "tracking_code": str(result_data.get("tracking_code", "")),
        "status": str(result_data.get("order_status", "pending")),
        "raw_response": data,
    }


def track_order(order, courier) -> dict[str, Any]:
    """
    Retrieve tracking information for a Pathao order.

    Returns dict with keys: status, details, raw_response.
    """
    base = _base_url(courier)
    consignment_id = order.courier_consignment_id
    url = f"{base}{PATHAO_TRACKING_ENDPOINT.format(consignment_id=consignment_id)}"
    headers = _auth_headers(courier)

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    result_data = data.get("data", data)
    return {
        "status": str(result_data.get("order_status", order.courier_status)),
        "details": result_data,
        "raw_response": data,
    }
