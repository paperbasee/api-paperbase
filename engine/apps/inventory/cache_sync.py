from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Case, F, IntegerField, Q, Sum, Value, When
from django.db.models.functions import Least

from engine.apps.products.models import Product

from .models import Inventory
from .utils import MAX_STOCK_QUANTITY, clamp_stock

logger = logging.getLogger(__name__)

_SYNC_BATCH_SIZE = 750


def _row_contribution_case():
    """SQL expression: per-inventory-row contribution (inactive variants → 0)."""
    cap = Value(MAX_STOCK_QUANTITY)
    capped_qty = Least(F("quantity"), cap)
    return Case(
        When(Q(variant__isnull=True), then=capped_qty),
        When(variant__is_active=True, then=capped_qty),
        default=Value(0),
        output_field=IntegerField(),
    )


def _expected_stock_by_product_id(*, store_id: int, product_ids: list) -> dict:
    """Map product_id → clamped total from Inventory (empty ids → {})."""
    if not product_ids:
        return {}
    rc = _row_contribution_case()
    rows = (
        Inventory.objects.filter(product__store_id=store_id, product_id__in=product_ids)
        .values("product_id")
        .annotate(total=Sum(rc))
    )
    return {row["product_id"]: clamp_stock(int(row["total"] or 0)) for row in rows}


def refresh_product_stock_cache(*, store_id: int, product_id) -> None:
    """
    Recompute Product.stock from Inventory for one product.

    Uses aggregation (no full inventory queryset in Python).
    Short transaction: lock product row, then write derived stock.
    """
    rc = _row_contribution_case()
    agg = Inventory.objects.filter(
        product_id=product_id,
        product__store_id=store_id,
    ).aggregate(total=Sum(rc))
    expected = clamp_stock(int(agg["total"] or 0))
    with transaction.atomic():
        locked = (
            Product.objects.select_for_update()
            .filter(id=product_id, store_id=store_id)
            .only("id")
            .first()
        )
        if not locked:
            return
        Product.objects.filter(id=product_id, store_id=store_id).update(stock=expected)


def _sync_product_stock_cache_batch(*, store_id: int, product_ids: list) -> None:
    if not product_ids:
        return
    with transaction.atomic():
        products = list(
            Product.objects.select_for_update()
            .filter(store_id=store_id, id__in=product_ids)
            .only("id", "stock")
        )
        if not products:
            return
        locked_ids = [p.id for p in products]
        totals = _expected_stock_by_product_id(store_id=store_id, product_ids=locked_ids)
        changed: list[Product] = []
        for p in products:
            expected = totals.get(p.id, 0)
            if int(p.stock) != expected:
                logger.info(
                    "Reconciled product stock cache from inventory (full store sync)",
                    extra={
                        "store_id": store_id,
                        "product_id": str(p.id),
                        "expected_stock": expected,
                        "previous_stock": int(p.stock),
                    },
                )
                p.stock = expected
                changed.append(p)
        if changed:
            Product.objects.bulk_update(changed, ["stock"])


def sync_product_stock_cache(store_id: int) -> None:
    """
    Synchronize Product.stock from Inventory.quantity.

    Batched transactions and aggregation only — no full-store list() or
    inventory-wide select_for_update.
    """
    batch: list = []
    qs = Product.objects.filter(store_id=store_id).order_by("id").values_list("id", flat=True)
    for pid in qs.iterator(chunk_size=_SYNC_BATCH_SIZE):
        batch.append(pid)
        if len(batch) >= _SYNC_BATCH_SIZE:
            _sync_product_stock_cache_batch(store_id=store_id, product_ids=batch)
            batch = []
    if batch:
        _sync_product_stock_cache_batch(store_id=store_id, product_ids=batch)
