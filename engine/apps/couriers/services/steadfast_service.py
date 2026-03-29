"""
Steadfast (Packzy) courier integration service.

Sends orders to Steadfast via their REST API.
Decryption of stored credentials happens exclusively inside this module.
"""

from __future__ import annotations

import logging
from typing import Any

import requests
from rest_framework.exceptions import ValidationError

logger = logging.getLogger(__name__)

from engine.core.encryption import decrypt_value

STEADFAST_BASE_URL = "https://portal.packzy.com/api/v1"

RECIPIENT_NAME_MAX_LEN = 100
RECIPIENT_ADDRESS_MAX_LEN = 250


def normalize_phone_number(phone: str) -> str:
    """
    Normalize to 11-digit BD local format (e.g. 01712345678).
    Handles +880, 880 prefix, and 10-digit without leading 0.
    """
    if not phone:
        return ""
    digits_only = "".join(c for c in phone if c.isdigit())
    if digits_only.startswith("880") and len(digits_only) == 13:
        digits_only = digits_only[3:]
    if len(digits_only) == 10 and not digits_only.startswith("0"):
        digits_only = "0" + digits_only
    return digits_only


def _auth_headers(courier) -> dict[str, str]:
    api_key = decrypt_value(courier.api_key_encrypted)
    secret_key = decrypt_value(courier.secret_key_encrypted)
    return {
        "Api-Key": api_key,
        "Secret-Key": secret_key,
        "Content-Type": "application/json",
    }


def _recipient_address_for_steadfast(order) -> str:
    """
    Use stored shipping_address; if order.district is set and not already the last segment,
    append ", {district}" so Packzy gets village/thana/district in one line (max 250 chars).
    """
    raw = (getattr(order, "shipping_address", None) or "").strip()
    d = (getattr(order, "district", None) or "").strip()
    if not d:
        combined = raw
    elif not raw:
        combined = d
    else:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if parts and parts[-1].casefold() == d.casefold():
            combined = raw
        else:
            combined = f"{raw}, {d}"
    return combined[:RECIPIENT_ADDRESS_MAX_LEN]


def _extract_consignment_id(parsed: dict[str, Any]) -> str:
    """
    Read consignment reference from Packzy create_order JSON.

    Documented / observed shapes include:
    - ``{"data": {"consignment_id": "..."}}``
    - ``{"consignment_id": "..."}`` at top level
    - ``{"consignment": {"consignment_id": ...}}`` (nested object, id may be int)
    - ``{"data": {"consignment": {"consignment_id": ..., "tracking_code": ...}}}``
    """
    if not isinstance(parsed, dict):
        return ""

    def from_mapping(obj: Any) -> str:
        if not isinstance(obj, dict):
            return ""
        cid = obj.get("consignment_id")
        if cid is not None and str(cid).strip():
            return str(cid).strip()
        nested = obj.get("consignment")
        if isinstance(nested, dict):
            cid = nested.get("consignment_id")
            if cid is not None and str(cid).strip():
                return str(cid).strip()
            # Some Steadfast responses expose only a tracking code alongside numeric id
            tc = nested.get("tracking_code")
            if tc is not None and str(tc).strip():
                return str(tc).strip()
        return ""

    inner = parsed.get("data")
    if isinstance(inner, dict):
        hit = from_mapping(inner)
        if hit:
            return hit
    return from_mapping(parsed)


def build_create_order_payload(order) -> dict[str, Any]:
    """
    Packzy create_order body — five core keys only (legacy shape):
    invoice, recipient_name, recipient_phone, recipient_address, cod_amount.
    recipient_address merges shipping_address with district when needed, then caps at 250 chars.
    """
    recipient_phone = normalize_phone_number((order.phone or "").strip())
    if not recipient_phone or not recipient_phone.isdigit() or len(recipient_phone) != 11:
        raise ValidationError(
            {"detail": "Steadfast requires an 11-digit Bangladesh mobile number (e.g. 01XXXXXXXXX)."}
        )

    full_name = ((order.shipping_name or "Customer").strip() or "Customer")[:RECIPIENT_NAME_MAX_LEN]
    full_address = _recipient_address_for_steadfast(order)

    return {
        "invoice": str(order.order_number),
        "recipient_name": full_name,
        "recipient_phone": recipient_phone,
        "recipient_address": full_address,
        "cod_amount": float(order.total),
    }


def create_order(order, courier) -> dict[str, Any]:
    """
    Create an order on Steadfast.

    Returns dict with keys: consignment_id, raw_response.
    Raises ValidationError for invalid order data; requests.HTTPError on HTTP failure.
    """
    url = f"{STEADFAST_BASE_URL}/create_order"
    payload = build_create_order_payload(order)
    headers = _auth_headers(courier)

    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        logger.warning("Steadfast create_order: JSON root is not an object; keys unavailable")
        data = {}

    consignment_id = _extract_consignment_id(data)
    if not consignment_id:
        logger.warning(
            "Steadfast create_order: success response but no consignment_id parsed; raw keys=%s",
            list(data.keys()) if isinstance(data, dict) else type(data).__name__,
        )

    return {
        "consignment_id": consignment_id,
        "raw_response": data,
    }
