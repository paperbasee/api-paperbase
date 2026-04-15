"""
Deterministic Meta (Facebook) Conversions API ``event_id`` values.

All IDs are stable for the same underlying entity / session / query so the
storefront Pixel can send the same ``eventID`` and Meta can dedupe browser
and server events.

Frontend mirror (TypeScript-style)::

    // Purchase — after order API returns ``public_id`` (e.g. ord_…)
    const purchaseEventId = `purchase_${orderPublicId}`;

    // ViewContent — product detail ``public_id`` (e.g. prd_…)
    const viewEventId = `view_${productPublicId}`;

    // InitiateCheckout — Django session key (32-char session id).
    // Browsers usually cannot read HttpOnly ``sessionid``; prefer POST
    // ``…/initiate-checkout/`` with credentials and use ``meta_event_id``
    // from the JSON response for Pixel ``eventID``.
    const checkoutEventId = `checkout_${sessionKey}`;

    // Search — sha256 hex of ``normalize(q) + \"\\x1e\" + sessionKey`` (UTF-8),
    // same normalization as backend (trim, lower, collapse whitespace).
    import { createHash } from \"crypto\"; // Node; in browser use subtle.digest or a small sha256 lib
    function searchEventId(q: string, sessionKey: string) {
      const n = q.trim().toLowerCase().replace(/\\s+/g, \" \");
      const payload = `${n}\\x1e${sessionKey}`;
      return `search_${createHash(\"sha256\").update(payload, \"utf8\").digest(\"hex\")}`;
    }

No random UUIDs: callers must obtain a string from the builders (or skip
tracking when a builder returns ``None`` — e.g. missing session).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_PURCHASE_RE = re.compile(r"^purchase_ord_[0-9a-f]{20}$")
# Django session keys are from get_random_string; length is typically 32.
_CHECKOUT_RE = re.compile(r"^checkout_[A-Za-z0-9]{8,128}$")
_VIEW_RE = re.compile(r"^view_prd_[0-9a-f]{20}$")
_SEARCH_RE = re.compile(r"^search_[0-9a-f]{64}$")


def normalize_search_query(query: str) -> str:
    """Single canonical form for search hashing (trim, lower, collapse spaces)."""
    return " ".join((query or "").strip().lower().split())


def _session_key_or_none(request: Any) -> str | None:
    session = getattr(request, "session", None)
    if session is None:
        return None
    key = getattr(session, "session_key", None) or ""
    if not key:
        try:
            session.save()
        except Exception:
            return None
        key = getattr(session, "session_key", None) or ""
    return key or None


def build_purchase_event_id(order: Any) -> str:
    """``purchase_<order.public_id>`` (e.g. purchase_ord_…)."""
    public_id = (getattr(order, "public_id", None) or "").strip()
    if not public_id:
        raise ValueError("order.public_id required for Meta Purchase event_id")
    return f"purchase_{public_id}"


def build_checkout_event_id(request: Any) -> str | None:
    """``checkout_<django_session_key>`` — needs SessionMiddleware + persistable session."""
    key = _session_key_or_none(request)
    if not key:
        return None
    return f"checkout_{key}"


def build_view_content_event_id(product: Any) -> str | None:
    """``view_<product.public_id>`` (e.g. view_prd_…)."""
    public_id = (getattr(product, "public_id", None) or "").strip()
    if not public_id:
        return None
    return f"view_{public_id}"


def build_search_event_id(request: Any, query: str) -> str | None:
    """
    ``search_<sha256_hex>`` over UTF-8 ``normalize(query) + \"\\x1e\" + session_key``.

    Same query + same session yields the same id (Pixel must use the same inputs).
    """
    key = _session_key_or_none(request)
    if not key:
        return None
    nq = normalize_search_query(query)
    payload = f"{nq}\x1e{key}".encode("utf-8")
    return f"search_{hashlib.sha256(payload).hexdigest()}"


def meta_event_id_valid(event_name: str, event_id: str) -> bool:
    """Return True if ``event_id`` matches the expected pattern for ``event_name``."""
    if not event_id or not isinstance(event_id, str):
        return False
    eid = event_id.strip()
    if not eid:
        return False
    if event_name == "Purchase":
        return bool(_PURCHASE_RE.match(eid))
    if event_name == "InitiateCheckout":
        return bool(_CHECKOUT_RE.match(eid))
    if event_name == "ViewContent":
        return bool(_VIEW_RE.match(eid))
    if event_name == "Search":
        return bool(_SEARCH_RE.match(eid))
    return False
