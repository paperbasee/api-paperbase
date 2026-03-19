"""
Seed realistic apparel: 1 shirt (4 colors × 5 sizes = 20 variants) and
1 pant (waist 32–38 × Regular/Slim = 14 variants), with SKUs and extra_data.

Usage:
  python manage.py seed_apparel_demo
  python manage.py seed_apparel_demo --store-id 4
  python manage.py seed_apparel_demo --force   # remove prior demo products by name and re-seed
"""

from __future__ import annotations

import random
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from engine.apps.products.models import (
    Category,
    Product,
    ProductAttribute,
    ProductAttributeValue,
    ProductVariant,
    ProductVariantAttribute,
)
from engine.apps.stores.models import Store, StoreSettings


SHIRT_NAME = "Classic Crew Neck T-Shirt"
PANT_NAME = "Stretch Chino Pant"

# Global attribute slugs (unique) — prefixed to avoid clashing with existing catalog data
ATTR_SHIRT_COLOR = "demo-shirt-color"
ATTR_SHIRT_SIZE = "demo-shirt-size"
ATTR_PANT_WAIST = "demo-pant-waist"
ATTR_PANT_FIT = "demo-pant-fit"

SHIRT_COLORS = [
    ("Black", "BLK"),
    ("White", "WHT"),
    ("Navy", "NVY"),
    ("Burgundy", "BRG"),
]
SHIRT_SIZES = ["XS", "S", "M", "L", "XL"]

PANT_WAISTS = ["32", "33", "34", "35", "36", "37", "38"]
PANT_FITS = [
    ("Regular", "REG"),
    ("Slim", "SLM"),
]


class Command(BaseCommand):
    help = "Seed demo shirt (20 variants) and pant (14 variants) with SKUs and extra_data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--store-id",
            type=int,
            default=None,
            help="Store primary key (default: first active store)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Delete existing demo products (by name) for this store and re-seed",
        )

    def handle(self, *args, **options):
        store_id = options.get("store_id")
        force = options.get("force", False)

        if store_id is not None:
            store = Store.objects.filter(pk=store_id, is_active=True).first()
            if not store:
                self.stderr.write(self.style.ERROR(f"No active store with id={store_id}"))
                return
        else:
            store = Store.objects.filter(is_active=True).order_by("id").first()
            if not store:
                self.stderr.write(self.style.ERROR("No active store found."))
                return

        self.stdout.write(f"Using store: {store.name!r} (id={store.pk})")

        with transaction.atomic():
            if force:
                self._delete_demo_products(store)

            shirt_cat, pant_cat = self._ensure_categories(store)
            color_attr, size_attr, waist_attr, fit_attr = self._ensure_attributes()
            self._merge_demo_extra_schema(store)

            shirt = self._ensure_shirt(store, shirt_cat, color_attr, size_attr)
            pant = self._ensure_pant(store, pant_cat, waist_attr, fit_attr)

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Shirt variants: {shirt.variants.count()}, "
                f"Pant variants: {pant.variants.count()}"
            )
        )

    def _delete_demo_products(self, store: Store) -> None:
        qs = Product.objects.filter(store=store, name__in=[SHIRT_NAME, PANT_NAME])
        n, _ = qs.delete()
        if n:
            self.stdout.write(self.style.WARNING(f"Removed {n} demo product rows (and dependents)."))

    def _ensure_categories(self, store: Store) -> tuple[Category, Category]:
        parent, _ = Category.objects.get_or_create(
            store=store,
            slug="apparel",
            defaults={
                "name": "Apparel",
                "description": "Clothing and basics",
                "order": 0,
                "is_active": True,
                "parent": None,
            },
        )
        shirt_cat, _ = Category.objects.get_or_create(
            store=store,
            slug="shirts",
            defaults={
                "name": "Shirts",
                "description": "Tops and tees",
                "order": 1,
                "is_active": True,
                "parent": parent,
            },
        )
        pant_cat, _ = Category.objects.get_or_create(
            store=store,
            slug="pants",
            defaults={
                "name": "Pants",
                "description": "Bottoms and chinos",
                "order": 2,
                "is_active": True,
                "parent": parent,
            },
        )
        return shirt_cat, pant_cat

    def _ensure_attributes(self) -> tuple[
        ProductAttribute,
        ProductAttribute,
        ProductAttribute,
        ProductAttribute,
    ]:
        color_attr, _ = ProductAttribute.objects.get_or_create(
            slug=ATTR_SHIRT_COLOR,
            defaults={"name": "Shirt color (demo)", "order": 1},
        )
        size_attr, _ = ProductAttribute.objects.get_or_create(
            slug=ATTR_SHIRT_SIZE,
            defaults={"name": "Shirt size (demo)", "order": 2},
        )
        waist_attr, _ = ProductAttribute.objects.get_or_create(
            slug=ATTR_PANT_WAIST,
            defaults={"name": "Pant waist (demo)", "order": 3},
        )
        fit_attr, _ = ProductAttribute.objects.get_or_create(
            slug=ATTR_PANT_FIT,
            defaults={"name": "Pant fit (demo)", "order": 4},
        )

        for order, (label, code) in enumerate(SHIRT_COLORS):
            ProductAttributeValue.objects.get_or_create(
                attribute=color_attr,
                value=label,
                defaults={"order": order},
            )
        for order, sz in enumerate(SHIRT_SIZES):
            ProductAttributeValue.objects.get_or_create(
                attribute=size_attr,
                value=sz,
                defaults={"order": order},
            )
        for order, w in enumerate(PANT_WAISTS):
            ProductAttributeValue.objects.get_or_create(
                attribute=waist_attr,
                value=w,
                defaults={"order": order},
            )
        for order, (label, _code) in enumerate(PANT_FITS):
            ProductAttributeValue.objects.get_or_create(
                attribute=fit_attr,
                value=label,
                defaults={"order": order},
            )

        return color_attr, size_attr, waist_attr, fit_attr

    def _merge_demo_extra_schema(self, store: Store) -> None:
        """Optional: add sample schema keys so dashboard extra fields match extra_data."""
        settings, _ = StoreSettings.objects.get_or_create(store=store)
        schema = list(settings.extra_field_schema or [])
        if not isinstance(schema, list):
            schema = []

        def has(name: str, entity: str = "product") -> bool:
            for row in schema:
                if not isinstance(row, dict):
                    continue
                if (row.get("name") or "").strip() == name and (
                    row.get("entityType") or row.get("entity_type")
                ) == entity:
                    return True
            return False

        next_order = max((int(r.get("order") or 0) for r in schema if isinstance(r, dict)), default=-1)

        def add_field(
            name: str,
            field_type: str,
            required: bool = False,
            options: list[str] | None = None,
        ) -> None:
            nonlocal next_order
            if has(name):
                return
            next_order += 1
            schema.append(
                {
                    "id": f"seed-{name.lower().replace(' ', '-')}",
                    "entityType": "product",
                    "name": name,
                    "fieldType": field_type,
                    "required": required,
                    "order": next_order,
                    **({"options": options} if options else {}),
                }
            )

        add_field("Material", "text")
        add_field("Care", "text")
        add_field("GSM", "text")  # shirt weight / fabric weight
        add_field("Inseam", "text")
        add_field("Rise", "dropdown", options=["Low-rise", "Mid-rise", "High-rise"])

        settings.extra_field_schema = schema
        settings.save(update_fields=["extra_field_schema"])

    def _value_map(self, attr: ProductAttribute) -> dict[str, ProductAttributeValue]:
        return {v.value: v for v in attr.values.all()}

    def _ensure_shirt(
        self,
        store: Store,
        category: Category,
        color_attr: ProductAttribute,
        size_attr: ProductAttribute,
    ) -> Product:
        existing = Product.objects.filter(store=store, name=SHIRT_NAME).first()
        if existing and existing.variants.count() == len(SHIRT_COLORS) * len(SHIRT_SIZES):
            self._sync_parent_stock(existing)
            self.stdout.write("Shirt already fully seeded; refreshed parent stock from variants.")
            return existing

        if existing:
            existing.variants.all().delete()
            p = existing
        else:
            p = Product(
                store=store,
                name=SHIRT_NAME,
                brand="Gadzilla Essentials",
                sku="GADZ-TS-PARENT",
                price=Decimal("24.99"),
                original_price=Decimal("32.00"),
                category=category,
                description=(
                    "Soft crew neck tee for everyday wear. "
                    "Pre-shrunk cotton blend. Available in multiple colors and sizes."
                ),
                stock=0,
                stock_tracking=True,
                status=Product.Status.ACTIVE,
                badge=Product.Badge.NEW,
                is_featured=True,
                is_active=True,
                extra_data={
                    "Material": "100% ringspun cotton",
                    "GSM": "180 GSM",
                    "Care": "Machine wash cold with like colors. Tumble dry low.",
                },
            )
            p.save()

        colors = self._value_map(color_attr)
        sizes = self._value_map(size_attr)

        for color_label, color_code in SHIRT_COLORS:
            for sz in SHIRT_SIZES:
                sku = f"GADZ-TS-{color_code}-{sz}"
                cv = colors[color_label]
                sv = sizes[sz]
                v = ProductVariant.objects.create(
                    product=p,
                    sku=sku,
                    price_override=None,
                    stock_quantity=random.randint(8, 85),
                    is_active=True,
                )
                ProductVariantAttribute.objects.create(variant=v, attribute_value=cv)
                ProductVariantAttribute.objects.create(variant=v, attribute_value=sv)

        self._sync_parent_stock(p)
        self.stdout.write(self.style.SUCCESS(f"Shirt: {p.name} — {p.variants.count()} variants"))
        return p

    def _ensure_pant(
        self,
        store: Store,
        category: Category,
        waist_attr: ProductAttribute,
        fit_attr: ProductAttribute,
    ) -> Product:
        expected = len(PANT_WAISTS) * len(PANT_FITS)
        existing = Product.objects.filter(store=store, name=PANT_NAME).first()
        if existing and existing.variants.count() == expected:
            self._sync_parent_stock(existing)
            self.stdout.write("Pants already fully seeded; refreshed parent stock from variants.")
            return existing

        if existing:
            existing.variants.all().delete()
            p = existing
        else:
            p = Product(
                store=store,
                name=PANT_NAME,
                brand="Gadzilla Essentials",
                sku="GADZ-CH-PARENT",
                price=Decimal("59.99"),
                original_price=Decimal("78.00"),
                category=category,
                description=(
                    "Stretch chino with a clean profile. "
                    "Waist sizes 32–38 in Regular or Slim fit. Standard 32\" inseam."
                ),
                stock=0,
                stock_tracking=True,
                status=Product.Status.ACTIVE,
                badge=None,
                is_featured=True,
                is_active=True,
                extra_data={
                    "Material": "97% cotton, 3% elastane",
                    "Care": "Machine wash cold. Warm iron if needed.",
                    "Inseam": '32" (hem service available in-store)',
                    "Rise": "Mid-rise",
                },
            )
            p.save()

        waists = self._value_map(waist_attr)
        fits = self._value_map(fit_attr)

        for w in PANT_WAISTS:
            for fit_label, fit_code in PANT_FITS:
                sku = f"GADZ-CH-{w}-{fit_code}"
                wv = waists[w]
                fv = fits[fit_label]
                v = ProductVariant.objects.create(
                    product=p,
                    sku=sku,
                    price_override=Decimal("64.99") if fit_code == "SLM" else None,
                    stock_quantity=random.randint(5, 40),
                    is_active=True,
                )
                ProductVariantAttribute.objects.create(variant=v, attribute_value=wv)
                ProductVariantAttribute.objects.create(variant=v, attribute_value=fv)

        self._sync_parent_stock(p)
        self.stdout.write(self.style.SUCCESS(f"Pants: {p.name} — {p.variants.count()} variants"))
        return p

    def _sync_parent_stock(self, product: Product) -> None:
        """Mirror sum of variant stock on Product.stock for simple listings / legacy UIs."""
        from django.db.models import Sum

        total = product.variants.aggregate(s=Sum("stock_quantity"))["s"] or 0
        Product.objects.filter(pk=product.pk).update(stock=int(total))
        product.stock = int(total)
