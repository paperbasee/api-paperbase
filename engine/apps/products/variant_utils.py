"""Storefront variant selection rules shared by orders and pricing."""

from __future__ import annotations

from collections.abc import Sequence

from engine.apps.products.models import Product, ProductVariant


def _prefetched_active_variants(product: Product) -> Sequence[ProductVariant] | None:
    prefetched = getattr(product, "active_variants_prefetched", None)
    if prefetched is None:
        return None
    if isinstance(prefetched, Sequence):
        return prefetched
    return None


def product_has_active_variants(product: Product) -> bool:
    prefetched = _prefetched_active_variants(product)
    if prefetched is not None:
        return len(prefetched) > 0
    return product.variants.filter(is_active=True).exists()


def resolve_storefront_variant(
    *,
    product: Product,
    variant_public_id: str | None,
) -> ProductVariant | None:
    """
    Enforce variant rules for a single product line.

    - If the product has active variants, variant_public_id is required and must match.
    - If the product has no active variants, variant_public_id must be empty.

    Raises rest_framework.serializers.ValidationError with {"error": "..."} on failure.
    """
    from rest_framework import serializers

    raw = (variant_public_id or "").strip()
    has_variants = product_has_active_variants(product)

    if has_variants:
        if not raw:
            raise serializers.ValidationError(
                {"error": "Variant selection required for this product"}
            )
        prefetched = _prefetched_active_variants(product)
        if prefetched is not None:
            variant = next((v for v in prefetched if v.public_id == raw), None)
            if not variant:
                raise serializers.ValidationError(
                    {"error": "Invalid or inactive variant for this product."}
                )
            return variant
        variant = (
            ProductVariant.objects.filter(
                public_id=raw,
                product_id=product.pk,
                is_active=True,
            )
            .select_related("product")
            .first()
        )
        if not variant:
            raise serializers.ValidationError(
                {"error": "Invalid or inactive variant for this product."}
            )
        return variant

    if raw:
        raise serializers.ValidationError(
            {
                "error": "This product does not use variants; omit variant_public_id."
            }
        )
    return None


def unit_price_for_line(product: Product, variant: ProductVariant | None):
    """Snapshot unit price used at checkout (matches order line logic)."""
    if variant is not None:
        return variant.effective_price
    return product.price
