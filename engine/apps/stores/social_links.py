"""Normalize social profile URLs stored under StoreSettings.storefront_public[\"social_links\"]."""

from __future__ import annotations

import json
from typing import Any

# Keys accepted from dashboard / API (order is stable for UIs).
SOCIAL_LINK_PLATFORM_KEYS: tuple[str, ...] = (
    "facebook",
    "instagram",
    "twitter",
    "youtube",
    "linkedin",
    "tiktok",
    "pinterest",
    "website",
)

_MAX_URL_LEN = 500


def default_social_links() -> dict[str, str]:
    return {k: "" for k in SOCIAL_LINK_PLATFORM_KEYS}


def normalize_social_links_from_storefront_public(storefront_public: dict | None) -> dict[str, str]:
    """Read storefront_public JSON and return a full platform dict with string values."""
    out = default_social_links()
    if not isinstance(storefront_public, dict):
        return out
    raw = storefront_public.get("social_links")
    if not isinstance(raw, dict):
        return out
    for key in SOCIAL_LINK_PLATFORM_KEYS:
        val = raw.get(key)
        if isinstance(val, str):
            out[key] = val.strip()[:_MAX_URL_LEN]
    return out


def coerce_social_links_patch(raw: Any) -> dict[str, str]:
    """
    Parse PATCH payload: dict, or JSON string (e.g. from multipart).
    Unknown keys are ignored; values must be strings (non-strings coerced to "").
    """
    if raw is None:
        return default_social_links()
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return default_social_links()
        raw = json.loads(s)
    if not isinstance(raw, dict):
        raise ValueError("social_links must be a JSON object")
    out = default_social_links()
    for key in SOCIAL_LINK_PLATFORM_KEYS:
        if key not in raw:
            continue
        val = raw[key]
        if val is None:
            out[key] = ""
        elif isinstance(val, str):
            out[key] = val.strip()[:_MAX_URL_LEN]
        else:
            raise ValueError(f"social_links.{key} must be a string")
    return out
