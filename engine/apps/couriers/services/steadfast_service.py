"""
Steadfast (Packzy) courier integration service.

Sends orders to Steadfast via their REST API and retrieves tracking status.
Decryption of stored credentials happens exclusively inside this module.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from engine.core.encryption import decrypt_value

logger = logging.getLogger(__name__)

STEADFAST_BASE_URL = "https://portal.packzy.com/api/v1"


def _auth_headers(courier) -> dict[str, str]:
    api_key = decrypt_value(courier.api_key_encrypted)
    secret_key = decrypt_value(courier.secret_key_encrypted)
    return {
        "Api-Key": api_key,
        "Secret-Key": secret_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _build_payload(order) -> dict[str, Any]:
    """Build Steadfast create_order payload from an Order instance."""
    return {
        "invoice": order.order_number,
        "recipient_name": order.shipping_name or "Customer",
        "recipient_phone": order.phone,
        "recipient_address": order.shipping_address,
        "cod_amount": float(order.total),
        "note": "",
    }


def create_order(order, courier) -> dict[str, Any]:
    """
    Create an order on Steadfast.

    Returns dict with keys: consignment_id, tracking_code, status, raw_response.
    Raises requests.HTTPError on failure.
    """
    url = f"{STEADFAST_BASE_URL}/create_order"
    payload = _build_payload(order)
    headers = _auth_headers(courier)

    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    result_data = data.get("data", data)
    return {
        "consignment_id": str(result_data.get("consignment_id", "")),
        "tracking_code": str(result_data.get("tracking_code", "")),
        "status": str(result_data.get("status", "pending")),
        "raw_response": data,
    }


def track_order(order, courier) -> dict[str, Any]:
    """
    Retrieve tracking information for a Steadfast order.

    Uses the invoice-based status endpoint.
    Returns dict with keys: status, details, raw_response.
    """
    invoice = order.order_number
    url = f"{STEADFAST_BASE_URL}/status_by_invoice/{invoice}"
    headers = _auth_headers(courier)

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    result_data = data.get("data", data)
    return {
        "status": str(result_data.get("status", order.courier_status)),
        "details": result_data,
        "raw_response": data,
    }
