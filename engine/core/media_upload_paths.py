"""Tenant-scoped storage paths for FileField/ImageField upload_to callables."""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

# Default when upload has no usable extension (ImageField uploads; stable path keys).
_DEFAULT_MEDIA_EXTENSION = "jpg"


def _non_empty_public_id(value: str | None, *, label: str) -> str:
    if not (value or "").strip():
        raise ValueError(f"Missing or empty {label} for tenant media path (assign before save)")
    return value.strip()


def media_file_extension(filename: str) -> str:
    """
    Safe lowercase extension from the client filename only (basename; path segments ignored).

    Original name is never used in storage keys except to derive this suffix.
    Fallback: jpg when missing or unusable (e.g. "noext", "..", odd suffixes).
    """
    raw = (filename or "").strip()
    if not raw:
        return _DEFAULT_MEDIA_EXTENSION
    name = Path(raw).name
    if not name or name in {".", ".."}:
        return _DEFAULT_MEDIA_EXTENSION
    suffix = Path(name).suffix
    if not suffix:
        return _DEFAULT_MEDIA_EXTENSION
    ext = suffix.lower().lstrip(".")
    if not ext or len(ext) > 8:
        return _DEFAULT_MEDIA_EXTENSION
    if not ext.isalnum():
        return _DEFAULT_MEDIA_EXTENSION
    return ext


def tenant_product_main_upload_to(instance, filename: str) -> str:
    if not getattr(instance, "store_id", None):
        raise ValueError("Product missing store for upload path")
    store = instance.store
    if not store:
        raise ValueError("Product.store is required for upload path")
    store_pub = _non_empty_public_id(getattr(store, "public_id", None), label="store.public_id")
    product_pub = _non_empty_public_id(getattr(instance, "public_id", None), label="product.public_id")
    ext = media_file_extension(filename)
    unique = uuid.uuid4().hex[:16]
    return f"tenants/{store_pub}/products/{product_pub}/main_{unique}.{ext}"


def tenant_product_gallery_upload_to(instance, filename: str) -> str:
    if not getattr(instance, "product_id", None):
        raise ValueError("ProductImage missing product for upload path")
    product = instance.product
    if not product:
        raise ValueError("ProductImage.product is required for upload path")
    if not getattr(product, "store_id", None):
        raise ValueError("Product missing store for gallery upload path")
    store = product.store
    if not store:
        raise ValueError("Product.store is required for gallery upload path")
    store_pub = _non_empty_public_id(getattr(store, "public_id", None), label="store.public_id")
    product_pub = _non_empty_public_id(getattr(product, "public_id", None), label="product.public_id")
    image_pub = _non_empty_public_id(getattr(instance, "public_id", None), label="productimage.public_id")
    ext = media_file_extension(filename)
    return f"tenants/{store_pub}/products/{product_pub}/gallery/{image_pub}.{ext}"


def tenant_category_image_upload_to(instance, filename: str) -> str:
    if not getattr(instance, "store_id", None):
        raise ValueError("Category missing store for upload path")
    store = instance.store
    if not store:
        raise ValueError("Category.store is required for upload path")
    store_pub = _non_empty_public_id(getattr(store, "public_id", None), label="store.public_id")
    cat_pub = _non_empty_public_id(getattr(instance, "public_id", None), label="category.public_id")
    ext = media_file_extension(filename)
    return f"tenants/{store_pub}/categories/{cat_pub}.{ext}"


def tenant_banner_image_upload_to(instance, filename: str) -> str:
    if not getattr(instance, "store_id", None):
        raise ValueError("Banner missing store for upload path")
    store = instance.store
    if not store:
        raise ValueError("Banner.store is required for upload path")
    store_pub = _non_empty_public_id(getattr(store, "public_id", None), label="store.public_id")
    banner_pub = _non_empty_public_id(getattr(instance, "public_id", None), label="banner.public_id")
    ext = media_file_extension(filename)
    return f"tenants/{store_pub}/banners/{banner_pub}.{ext}"


def tenant_store_logo_upload_to(instance, filename: str) -> str:
    store_pub = _non_empty_public_id(getattr(instance, "public_id", None), label="store.public_id")
    ext = media_file_extension(filename)
    return f"tenants/{store_pub}/branding/logo.{ext}"


def tenant_support_attachment_upload_to(instance, filename: str) -> str:
    if not getattr(instance, "ticket_id", None):
        raise ValueError("SupportTicketAttachment missing ticket for upload path")
    ticket = instance.ticket
    if not ticket:
        raise ValueError("SupportTicketAttachment.ticket is required for upload path")
    if not getattr(ticket, "store_id", None):
        raise ValueError("Support ticket missing store for upload path")
    store = ticket.store
    if not store:
        raise ValueError("SupportTicket.store is required for upload path")
    store_pub = _non_empty_public_id(getattr(store, "public_id", None), label="store.public_id")
    ticket_pub = _non_empty_public_id(getattr(ticket, "public_id", None), label="ticket.public_id")
    att_pub = _non_empty_public_id(getattr(instance, "public_id", None), label="attachment.public_id")
    ext = media_file_extension(filename)
    return f"tenants/{store_pub}/support/{ticket_pub}/{att_pub}.{ext}"


def generate_order_export_file_path(
    store_public_id: str, export_date: date, job_id: uuid.UUID
) -> str:
    """
    Relative key under the default storage location (e.g. media/ in R2) for a store CSV export.

    Includes job_id in the filename so same-day re-exports stay unique with S3 file_overwrite off.
    """
    store_pub = _non_empty_public_id(store_public_id, label="store.public_id")
    date_str = export_date.isoformat()
    return f"tenants/{store_pub}/exports/order_{store_pub}_{date_str}__{job_id}.csv"
