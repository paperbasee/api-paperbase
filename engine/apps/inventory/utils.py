"""Inventory stock utility helpers."""

from __future__ import annotations

MIN_STOCK_QUANTITY = 0
MAX_STOCK_QUANTITY = 100000


def clamp_stock(value) -> int:
    """Clamp any incoming stock value to the allowed persistence range."""
    normalized = int(value)
    if normalized < MIN_STOCK_QUANTITY:
        return MIN_STOCK_QUANTITY
    if normalized > MAX_STOCK_QUANTITY:
        return MAX_STOCK_QUANTITY
    return normalized
