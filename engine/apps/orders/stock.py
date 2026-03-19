from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.db import transaction
from django.db.models import F
from django.core.exceptions import ValidationError

from engine.apps.products.models import Product, ProductVariant
from engine.apps.products.stock_sync import sync_product_stock_from_variants


@dataclass(frozen=True)
class StockTarget:
    product_id: str
    variant_id: Optional[int] = None


def _lock_product(product_id) -> Product:
    try:
        return Product.objects.select_for_update().get(pk=product_id)
    except Product.DoesNotExist:
        raise ValidationError({"product": f"Product {product_id} not found."})


def _lock_variant(variant_id) -> ProductVariant:
    try:
        return (
            ProductVariant.objects.select_for_update()
            .select_related("product")
            .get(pk=variant_id)
        )
    except ProductVariant.DoesNotExist:
        raise ValidationError({"variant": f"Variant {variant_id} not found."})


def adjust_stock(*, product_id, variant_id: int | None, delta_qty: int) -> None:
    """
    Adjust inventory for an order item.

    - delta_qty > 0: reduce stock (reserve/consume)
    - delta_qty < 0: restore stock (e.g. item removed or quantity lowered)
    """
    if delta_qty == 0:
        return
    if delta_qty is None:
        return
    if int(delta_qty) == 0:
        return
    delta_qty = int(delta_qty)

    with transaction.atomic():
        if variant_id is not None:
            variant = _lock_variant(variant_id)
            available = int(variant.stock_quantity)
            if delta_qty > 0 and available < delta_qty:
                raise ValidationError(
                    {"variant": f"Insufficient variant stock. Available: {available}, Requested: {delta_qty}"}
                )
            # Reduce stock when delta_qty positive; restore when negative.
            ProductVariant.objects.filter(pk=variant_id).update(
                stock_quantity=F("stock_quantity") - delta_qty
            )
            sync_product_stock_from_variants(variant.product_id)
            return

        product = _lock_product(product_id)
        available = int(product.stock)
        if delta_qty > 0 and available < delta_qty:
            raise ValidationError(
                {"product": f"Insufficient product stock. Available: {available}, Requested: {delta_qty}"}
            )
        Product.objects.filter(pk=product_id).update(stock=F("stock") - delta_qty)

