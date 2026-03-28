"""
Public ID generation for external-facing identifiers.

Uses a prefixed UUID-hex format: {prefix}_{20 hex chars}
Example: str_a8f3klm92xqp74d1e5b2

Internal integer PKs are kept for DB joins/FK performance.
Public IDs are used in all API responses, URLs, and webhooks.
"""

import uuid

_PREFIXES: dict[str, str] = {
    "user": "usr",
    "store": "str",
    "mbr": "mbr",
    "category": "cat",
    "product": "prd",
    "variant": "var",
    "image": "img",
    "attribute": "atr",
    "attrvalue": "atv",
    "customer": "cus",
    "address": "adr",
    "zone": "szn",
    "method": "smt",
    "rate": "srt",
    "ticket": "tkt",
    "banner": "ban",
    "plan": "pln",
    "subscription": "sub",
    "payment": "pay",
    "inventory": "inv",
    "stockmovement": "stm",
    "analytics": "anl",
    "order": "ord",
    "orderitem": "oit",
    "activitylog": "act",
    "notification": "cta",
    "systemnotification": "sys",
    "staffnotification": "snt",
    "systemnotificationview": "ntv",
    "storedeletionjob": "dlj",
    "attachment": "ath",
    "courier": "crr",
    "mktintegration": "mkt",
    "emailtemplate": "emt",
    "emaillog": "eml",
    "twofarecovery": "tfr",
    "storeapikey": "sak",
}


def generate_public_id(kind: str) -> str:
    """
    Generate a globally unique, URL-safe, prefixed public ID.

    Args:
        kind: Model kind key from _PREFIXES (e.g. "store", "product").

    Returns:
        String like "str_a8f3klm92xqp74d1e5b2" (24 chars max).
    """
    prefix = _PREFIXES[kind]
    return f"{prefix}_{uuid.uuid4().hex[:20]}"
