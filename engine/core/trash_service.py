from __future__ import annotations

import json
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from engine.apps.inventory.models import Inventory
from engine.apps.inventory.utils import clamp_stock
from engine.apps.orders.models import Order, OrderAddress, OrderItem
from engine.apps.products.models import (
    Product,
    ProductImage,
    ProductVariant,
    ProductVariantAttribute,
)
from engine.apps.stores.models import Store
from engine.core.admin_dashboard_cache import invalidate_notifications_and_dashboard_caches
from engine.core.media_deletion_service import schedule_media_deletion_from_keys
from engine.core.models import TrashItem
from engine.core.tenant_execution import system_scope

SNAPSHOT_SCHEMA_VERSION = 1
TRASH_RETENTION_DAYS = 15


def _decimal_json(d: Decimal | None) -> str | None:
    if d is None:
        return None
    return str(d)


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _dt_json(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _parse_dt(value: Any):
    if not value:
        return None
    from django.utils.dateparse import parse_datetime

    parsed = parse_datetime(str(value))
    return parsed


def _json_safe_snapshot(data: dict) -> dict:
    """Ensure JSONField can persist the snapshot (UUID, Decimal, datetime, etc.)."""
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def _collect_product_media_keys_from_snapshot(snapshot: dict) -> list[str]:
    keys: list[str] = []
    prod = snapshot.get("product") or {}
    main = prod.get("image") or ""
    if main:
        keys.append(str(main))
    for row in snapshot.get("images") or []:
        name = row.get("image") or ""
        if name:
            keys.append(str(name))
    return list(dict.fromkeys(keys))


def build_product_snapshot(product: Product) -> dict:
    product = (
        Product.objects.filter(pk=product.pk)
        .select_related("store", "category")
        .prefetch_related("images", "variants", "variants__attribute_values", "inventory_records")
        .get()
    )
    inv_rows = list(
        Inventory.objects.filter(product_id=product.pk).select_related("variant")
    )
    variant_attr_rows: list[dict[str, int]] = []
    for v in product.variants.all():
        for link in v.attribute_values.all():
            variant_attr_rows.append(
                {"variant_id": v.pk, "attribute_value_id": link.attribute_value_id}
            )
    prod_row = {
        "id": str(product.pk),
        "store_id": product.store_id,
        "public_id": product.public_id,
        "name": product.name,
        "brand": product.brand,
        "slug": product.slug,
        "price": _decimal_json(product.price),
        "original_price": _decimal_json(product.original_price),
        "image": product.image.name if product.image else "",
        "status": product.status,
        "category_id": product.category_id,
        "description": product.description,
        "stock": product.stock,
        "stock_tracking": product.stock_tracking,
        "is_active": product.is_active,
        "extra_data": product.extra_data or {},
        "created_at": _dt_json(product.created_at),
        "updated_at": _dt_json(product.updated_at),
    }
    images = [
        {
            "id": img.pk,
            "public_id": img.public_id,
            "image": img.image.name if img.image else "",
            "alt": img.alt,
            "order": img.order,
        }
        for img in product.images.all()
    ]
    variants = [
        {
            "id": v.pk,
            "public_id": v.public_id,
            "sku": v.sku,
            "price_override": _decimal_json(v.price_override),
            "is_active": v.is_active,
            "created_at": _dt_json(v.created_at),
            "updated_at": _dt_json(v.updated_at),
        }
        for v in product.variants.all()
    ]
    inventories = [
        {
            "id": inv.pk,
            "public_id": inv.public_id,
            "product_id": inv.product_id,
            "variant_id": inv.variant_id,
            "quantity": inv.quantity,
            "low_stock_threshold": inv.low_stock_threshold,
            "is_tracked": inv.is_tracked,
            "updated_at": _dt_json(inv.updated_at),
        }
        for inv in inv_rows
    ]
    return _json_safe_snapshot(
        {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "product": prod_row,
            "images": images,
            "variants": variants,
            "variant_attributes": variant_attr_rows,
            "inventories": inventories,
        }
    )


def build_order_snapshot(order: Order) -> dict:
    order = (
        Order.objects.filter(pk=order.pk)
        .select_related("store", "user", "customer", "shipping_zone", "shipping_method", "shipping_rate")
        .prefetch_related("items", "addresses")
        .get()
    )
    order_row = {
        "id": str(order.pk),
        "store_id": order.store_id,
        "public_id": order.public_id,
        "order_number": order.order_number,
        "user_id": order.user_id,
        "customer_id": order.customer_id,
        "email": order.email,
        "status": order.status,
        "total": _decimal_json(order.total),
        "subtotal_before_discount": _decimal_json(order.subtotal_before_discount),
        "discount_total": _decimal_json(order.discount_total),
        "subtotal_after_discount": _decimal_json(order.subtotal_after_discount),
        "shipping_cost": _decimal_json(order.shipping_cost),
        "shipping_zone_id": order.shipping_zone_id,
        "shipping_method_id": order.shipping_method_id,
        "shipping_rate_id": order.shipping_rate_id,
        "shipping_name": order.shipping_name,
        "shipping_address": order.shipping_address,
        "phone": order.phone,
        "district": order.district,
        "courier_provider": order.courier_provider,
        "courier_consignment_id": order.courier_consignment_id,
        "sent_to_courier": order.sent_to_courier,
        "customer_confirmation_sent_at": _dt_json(order.customer_confirmation_sent_at),
        "pricing_snapshot": order.pricing_snapshot or {},
        "created_at": _dt_json(order.created_at),
        "updated_at": _dt_json(order.updated_at),
    }
    items = [
        {
            "id": it.pk,
            "public_id": it.public_id,
            "product_id": str(it.product_id) if it.product_id else None,
            "variant_id": it.variant_id,
            "quantity": it.quantity,
            "unit_price": _decimal_json(it.unit_price),
            "original_price": _decimal_json(it.original_price),
            "discount_amount": _decimal_json(it.discount_amount),
            "line_subtotal": _decimal_json(it.line_subtotal),
            "line_total": _decimal_json(it.line_total),
        }
        for it in order.items.all()
    ]
    addresses = [
        {
            "address_type": a.address_type,
            "name": a.name,
            "phone": a.phone,
            "address_line1": a.address_line1,
            "address_line2": a.address_line2,
            "city": a.city,
            "region": a.region,
            "postal_code": a.postal_code,
            "country": a.country,
        }
        for a in order.addresses.all()
    ]
    return _json_safe_snapshot(
        {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "order": order_row,
            "items": items,
            "addresses": addresses,
        }
    )


def _expires_at():
    return timezone.now() + timedelta(days=TRASH_RETENTION_DAYS)


@transaction.atomic
def soft_delete_product(*, product: Product, store: Store, deleted_by) -> TrashItem:
    if product.store_id != store.id:
        raise ValidationError("Product does not belong to this store.")
    snap = build_product_snapshot(product)
    trash = TrashItem.objects.create(
        store=store,
        entity_type=TrashItem.EntityType.PRODUCT,
        entity_id=str(product.pk),
        entity_public_id=product.public_id,
        snapshot_json=snap,
        deleted_by=deleted_by,
        expires_at=_expires_at(),
    )
    Product.objects.filter(store_id=store.id, pk=product.pk).delete()
    from engine.apps.products.services import invalidate_product_cache

    invalidate_product_cache(store.public_id)
    return trash


@transaction.atomic
def soft_delete_order(*, order: Order, store: Store, deleted_by) -> TrashItem:
    if order.store_id != store.id:
        raise ValidationError("Order does not belong to this store.")
    snap = build_order_snapshot(order)
    store_public_id = store.public_id
    trash = TrashItem.objects.create(
        store=store,
        entity_type=TrashItem.EntityType.ORDER,
        entity_id=str(order.pk),
        entity_public_id=order.public_id,
        snapshot_json=snap,
        deleted_by=deleted_by,
        expires_at=_expires_at(),
    )
    Order.objects.filter(store_id=store.id, pk=order.pk).delete()
    invalidate_notifications_and_dashboard_caches(store_public_id)
    return trash


def hard_delete_product_for_admin(*, product: Product) -> None:
    store = product.store
    p = (
        Product.objects.filter(store_id=store.id, pk=product.pk)
        .prefetch_related("images")
        .first()
    )
    if not p:
        return
    media_keys = p.get_media_keys()
    p.delete()
    schedule_media_deletion_from_keys(media_keys)


def hard_delete_order_for_admin(*, order: Order) -> None:
    store = order.store
    store_public_id = store.public_id
    Order.objects.filter(store_id=store.id, pk=order.pk).delete()
    invalidate_notifications_and_dashboard_caches(store_public_id)


@transaction.atomic
def permanent_delete_trash_item(*, trash_item: TrashItem) -> None:
    store_id = trash_item.store_id
    snap = trash_item.snapshot_json or {}
    if trash_item.entity_type == TrashItem.EntityType.PRODUCT:
        pk = uuid.UUID(trash_item.entity_id)
        p = (
            Product.objects.filter(store_id=store_id, pk=pk)
            .prefetch_related("images")
            .first()
        )
        if p:
            media_keys = p.get_media_keys()
            p.delete()
            schedule_media_deletion_from_keys(media_keys)
        else:
            schedule_media_deletion_from_keys(
                _collect_product_media_keys_from_snapshot(snap)
            )
    elif trash_item.entity_type == TrashItem.EntityType.ORDER:
        pk = uuid.UUID(trash_item.entity_id)
        o = Order.objects.filter(store_id=store_id, pk=pk).first()
        if o:
            store_public_id = o.store.public_id
            o.delete()
            invalidate_notifications_and_dashboard_caches(store_public_id)
    trash_item.delete()


def _restore_product_from_snapshot(*, store: Store, snapshot: dict) -> Product:
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValidationError("Unsupported trash snapshot version.")
    prod = snapshot.get("product") or {}
    pid = uuid.UUID(str(prod["id"]))
    slug = prod.get("slug") or ""
    if Product.objects.filter(pk=pid).exists():
        raise ValidationError("A product with this id already exists; cannot restore.")
    if Product.objects.filter(store_id=store.id, slug=slug).exists():
        raise ValidationError(
            "Another product uses this slug; cannot restore. Rename the conflicting product first."
        )
    category_id = prod.get("category_id")
    from engine.apps.products.models import Category

    if not Category.objects.filter(pk=category_id, store_id=store.id).exists():
        raise ValidationError("Product category no longer exists; cannot restore.")

    p = Product(
        id=pid,
        store_id=store.id,
        public_id=prod["public_id"],
        name=prod["name"],
        brand=prod.get("brand") or None,
        slug=slug,
        price=_parse_decimal(prod.get("price")) or Decimal("0"),
        original_price=_parse_decimal(prod.get("original_price")),
        status=prod.get("status") or Product.Status.ACTIVE,
        category_id=category_id,
        description=prod.get("description") or "",
        stock=clamp_stock(prod.get("stock") or 0),
        stock_tracking=bool(prod.get("stock_tracking", True)),
        is_active=bool(prod.get("is_active", True)),
        extra_data=prod.get("extra_data") or {},
    )
    img_name = prod.get("image") or ""
    if img_name:
        p.image = img_name
    p.save()
    for row in snapshot.get("images") or []:
        img_path = row.get("image") or ""
        if not img_path:
            continue
        ProductImage.objects.create(
            id=row["id"],
            public_id=row["public_id"],
            product_id=p.pk,
            image=img_path,
            alt=row.get("alt") or "",
            order=int(row.get("order") or 0),
        )
    for row in snapshot.get("variants") or []:
        v = ProductVariant(
            id=row["id"],
            public_id=row["public_id"],
            product_id=p.pk,
            store_id=store.id,
            sku=row["sku"],
            price_override=_parse_decimal(row.get("price_override")),
            is_active=bool(row.get("is_active", True)),
        )
        v.save()
    for row in snapshot.get("inventories") or []:
        Inventory.objects.create(
            id=row["id"],
            public_id=row["public_id"],
            product_id=p.pk,
            variant_id=row.get("variant_id"),
            quantity=clamp_stock(row.get("quantity") or 0),
            low_stock_threshold=int(row.get("low_stock_threshold") or 5),
            is_tracked=bool(row.get("is_tracked", True)),
        )
    for link in snapshot.get("variant_attributes") or []:
        vid = link["variant_id"]
        av_id = link["attribute_value_id"]
        ProductVariantAttribute.objects.get_or_create(
            variant_id=vid,
            attribute_value_id=av_id,
        )
    return p


def _restore_order_from_snapshot(*, store: Store, snapshot: dict) -> Order:
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValidationError("Unsupported trash snapshot version.")
    o_row = snapshot.get("order") or {}
    oid = uuid.UUID(str(o_row["id"]))
    order_number = o_row.get("order_number") or ""
    public_id = o_row.get("public_id") or ""
    if Order.objects.filter(pk=oid).exists():
        raise ValidationError("An order with this id already exists; cannot restore.")
    if Order.objects.filter(order_number=order_number).exclude(pk=oid).exists():
        raise ValidationError(
            "Order number is already in use; cannot restore from trash."
        )
    if Order.objects.filter(public_id=public_id).exclude(pk=oid).exists():
        raise ValidationError("Order public_id conflict; cannot restore.")

    order = Order(
        id=oid,
        store_id=store.id,
        public_id=public_id,
        order_number=order_number,
        user_id=o_row.get("user_id"),
        customer_id=o_row.get("customer_id"),
        email=o_row.get("email") or "",
        status=o_row.get("status") or Order.Status.PENDING,
        total=_parse_decimal(o_row.get("total")) or Decimal("0"),
        subtotal_before_discount=_parse_decimal(o_row.get("subtotal_before_discount"))
        or Decimal("0"),
        discount_total=_parse_decimal(o_row.get("discount_total")) or Decimal("0"),
        subtotal_after_discount=_parse_decimal(o_row.get("subtotal_after_discount"))
        or Decimal("0"),
        shipping_cost=_parse_decimal(o_row.get("shipping_cost")) or Decimal("0"),
        shipping_zone_id=o_row["shipping_zone_id"],
        shipping_method_id=o_row.get("shipping_method_id"),
        shipping_rate_id=o_row.get("shipping_rate_id"),
        shipping_name=o_row.get("shipping_name") or "",
        shipping_address=o_row.get("shipping_address") or "",
        phone=o_row.get("phone") or "",
        district=o_row.get("district") or "",
        courier_provider=o_row.get("courier_provider") or "",
        courier_consignment_id=o_row.get("courier_consignment_id") or "",
        sent_to_courier=bool(o_row.get("sent_to_courier", False)),
        customer_confirmation_sent_at=_parse_dt(o_row.get("customer_confirmation_sent_at")),
        pricing_snapshot=o_row.get("pricing_snapshot") or {},
    )
    order.save()
    for row in snapshot.get("items") or []:
        pid = row.get("product_id")
        product_uuid = uuid.UUID(str(pid)) if pid else None
        product_pk = product_uuid if product_uuid and Product.objects.filter(pk=product_uuid).exists() else None
        variant_pk = row.get("variant_id")
        if variant_pk and not ProductVariant.objects.filter(pk=variant_pk).exists():
            variant_pk = None
        OrderItem.objects.create(
            id=row["id"],
            public_id=row["public_id"],
            order_id=order.pk,
            product_id=product_pk,
            variant_id=variant_pk,
            quantity=int(row["quantity"]),
            unit_price=_parse_decimal(row.get("unit_price")) or Decimal("0"),
            original_price=_parse_decimal(row.get("original_price")) or Decimal("0"),
            discount_amount=_parse_decimal(row.get("discount_amount")) or Decimal("0"),
            line_subtotal=_parse_decimal(row.get("line_subtotal")) or Decimal("0"),
            line_total=_parse_decimal(row.get("line_total")) or Decimal("0"),
        )
    for a in snapshot.get("addresses") or []:
        OrderAddress.objects.create(
            order_id=order.pk,
            address_type=a["address_type"],
            name=a["name"],
            phone=a.get("phone") or "",
            address_line1=a["address_line1"],
            address_line2=a.get("address_line2") or "",
            city=a["city"],
            region=a.get("region") or "",
            postal_code=a.get("postal_code") or "",
            country=a["country"],
        )
    return order


@transaction.atomic
def restore_trash_item(*, trash_item: TrashItem, store: Store) -> None:
    if trash_item.store_id != store.id:
        raise ValidationError("Trash item does not belong to this store.")
    if trash_item.is_restored:
        raise ValidationError("This item was already restored.")
    locked = (
        TrashItem.objects.select_for_update()
        .filter(pk=trash_item.pk, store_id=store.id, is_restored=False)
        .first()
    )
    if not locked:
        raise ValidationError("Trash item is no longer restorable.")
    snap = locked.snapshot_json or {}
    if locked.entity_type == TrashItem.EntityType.PRODUCT:
        _restore_product_from_snapshot(store=store, snapshot=snap)
    elif locked.entity_type == TrashItem.EntityType.ORDER:
        _restore_order_from_snapshot(store=store, snapshot=snap)
    else:
        raise ValidationError("Unknown entity type.")
    entity_type = locked.entity_type
    store_public_id = store.public_id
    store_id_int = int(store.id)

    locked.is_restored = True
    locked.save(update_fields=["is_restored"])

    # Defer cache work until after commit so we do not hold row locks while
    # sync_product_stock_cache select_for_update-scans the whole catalog (avoids
    # deadlocks when multiple restores run concurrently).
    if entity_type == TrashItem.EntityType.PRODUCT:

        def _after_commit_product():
            from engine.apps.inventory.cache_sync import sync_product_stock_cache
            from engine.apps.products.services import invalidate_product_cache

            invalidate_product_cache(store_public_id)
            sync_product_stock_cache(store_id_int)

        transaction.on_commit(_after_commit_product)
    else:

        def _after_commit_order():
            invalidate_notifications_and_dashboard_caches(store_public_id)

        transaction.on_commit(_after_commit_order)


def purge_expired_trash(*, now=None) -> int:
    """Delete expired trash rows and associated media. Returns number of rows processed."""
    now = now or timezone.now()
    count = 0
    with system_scope(reason="trash_expiry_purge"):
        qs = TrashItem.objects.filter(is_restored=False, expires_at__lt=now).order_by("id")
        for item in qs.iterator():
            with transaction.atomic():
                row = (
                    TrashItem.objects.select_for_update()
                    .filter(pk=item.pk, is_restored=False, expires_at__lt=now)
                    .first()
                )
                if not row:
                    continue
                permanent_delete_trash_item(trash_item=row)
                count += 1
    return count
