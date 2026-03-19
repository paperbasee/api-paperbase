"""
Resolve per-store extra field definitions for products (dashboard dynamic fields).

Schema is stored on StoreSettings.extra_field_schema as a list of dicts with keys
aligned to the dashboard: entityType, name, fieldType, required, order, options, defaultValue.
"""

from __future__ import annotations

from typing import Any

from engine.apps.stores.models import Store, StoreSettings


def get_product_extra_schema(store: Store | None) -> list[dict[str, Any]]:
    """Return sorted product entity field definitions for this store."""
    if store is None:
        return []
    try:
        settings = store.settings
    except StoreSettings.DoesNotExist:
        return []
    raw = settings.extra_field_schema
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        et = item.get("entityType") or item.get("entity_type") or "product"
        if et != "product":
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        out.append(item)
    out.sort(key=lambda x: (x.get("order") if isinstance(x.get("order"), int) else 0, x.get("name") or ""))
    return out


def form_field_name_for_schema_item(item_id: str) -> str:
    """Stable, safe Django form field name for a schema row."""
    safe = "".join(c if c.isalnum() else "_" for c in str(item_id))
    return f"extra_schema_{safe}"
