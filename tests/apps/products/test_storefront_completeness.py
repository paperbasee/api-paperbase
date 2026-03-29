from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from django.test import override_settings
from rest_framework.test import APIClient

from engine.apps.notifications.models import StorefrontCTA
from engine.apps.shipping.models import ShippingMethod, ShippingRate, ShippingZone
from engine.apps.stores.models import StoreSettings
from engine.apps.stores.services import create_store_api_key
from engine.core.tenant_execution import tenant_scope_from_store
from tests.apps.stores.test_api_keys import make_product, make_store


def _api_key_client(api_key: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {api_key}")
    return client


@override_settings(TENANT_API_KEY_ENFORCE=True)
@pytest.mark.django_db
def test_catalog_filters_store_public_and_search():
    store = make_store("StorefrontCX")
    settings_obj, _ = StoreSettings.objects.get_or_create(store=store)
    settings_obj.storefront_public = {
        "country": "BD",
        "theme_settings": {"primary_color": "#111111"},
    }
    settings_obj.modules_enabled = {"loyalty": True}
    settings_obj.extra_field_schema = [
        {"id": "fld_1", "entityType": "product", "name": "Warranty", "fieldType": "text"},
    ]
    settings_obj.save(
        update_fields=["storefront_public", "modules_enabled", "extra_field_schema"]
    )
    make_product(store, name="Alpha AX")
    _row, key = create_store_api_key(store, name="fe")
    client = _api_key_client(key)

    fr = client.get("/api/v1/catalog/filters/")
    assert fr.status_code == 200
    body = fr.json()
    assert set(body.keys()) >= {"categories", "attributes", "brands", "price_range"}
    assert body["price_range"]["min"] <= body["price_range"]["max"]
    assert body["categories"]
    cat0 = body["categories"][0]
    assert set(cat0.keys()) >= {"public_id", "name", "slug"}

    sr = client.get("/api/v1/store/public/")
    assert sr.status_code == 200
    pub = sr.json()
    assert pub["store_name"] == "StorefrontCX"
    assert pub["currency"] == "BDT"
    assert pub["country"] == "BD"
    assert pub["theme_settings"]["primary_color"] == "#111111"
    assert pub["modules_enabled"] == {"loyalty": True}
    assert len(pub["extra_field_schema"]) == 1
    assert pub["extra_field_schema"][0]["id"] == "fld_1"
    assert "social_links" in pub
    assert pub["social_links"]["facebook"] == ""
    assert "website" in pub["social_links"]

    z = client.get("/api/v1/search/?q=al")
    assert z.status_code == 200
    s = z.json()
    assert "products" in s and "categories" in s and "suggestions" in s
    assert s["trending"] is False

    tr = client.get("/api/v1/search/?trending=true")
    assert tr.status_code == 200
    assert tr.json()["trending"] is True


@override_settings(TENANT_API_KEY_ENFORCE=True)
@pytest.mark.django_db
def test_shipping_zones_and_product_detail_enrichment():
    store = make_store("ShipCX")
    p = make_product(store, name="Ship Product")
    with tenant_scope_from_store(store=store, reason="test fixture"):
        p.brand = "ShipBrand"
        p.sku = "SHIP-SKU-1"
        p.original_price = Decimal("199.00")
        p.save(update_fields=["brand", "sku", "original_price"])
        zone = ShippingZone.objects.create(
            store=store,
            name="Dhaka",
            is_active=True,
            estimated_delivery_text="1-2",
        )
        method = ShippingMethod.objects.create(store=store, name="Standard", is_active=True)
        method.zones.add(zone)
        ShippingRate.objects.create(
            store=store,
            shipping_method=method,
            shipping_zone=zone,
            price=Decimal("60.00"),
            min_order_total=None,
        )
    _row, key = create_store_api_key(store, name="fe")
    client = _api_key_client(key)

    zr = client.get("/api/v1/shipping/zones/")
    assert zr.status_code == 200
    zones = zr.json()
    assert len(zones) >= 1
    assert zones[0]["estimated_days"] == "1-2"
    assert zones[0]["cost_rules"]
    assert zones[0]["is_active"] is True
    assert "created_at" in zones[0] and "updated_at" in zones[0]

    lr = client.get("/api/v1/products/")
    assert lr.status_code == 200
    list_body = lr.json()
    rows = list_body.get("results", list_body)
    row = next(r for r in rows if r["public_id"] == p.public_id)
    for key in ("original_price", "brand", "sku", "available_quantity"):
        assert key in row
    assert row["brand"] == "ShipBrand"
    assert row["sku"] == "SHIP-SKU-1"
    assert row["original_price"] is not None
    assert int(row["available_quantity"]) >= 0

    dr = client.get(f"/api/v1/products/{p.public_id}/")
    assert dr.status_code == 200
    detail = dr.json()
    assert detail["breadcrumbs"][0] == "Home"
    assert detail["breadcrumbs"][-1] == "Ship Product"
    assert "related_products" in detail
    assert "variant_matrix" in detail
    assert "sku" in detail and "stock_tracking" in detail
    assert "category_public_id" in detail
    assert "category_slug" in detail
    assert "category_name" in detail
    assert "image_url" in detail
    assert "category" not in detail
    assert detail["stock_status"] in ("in_stock", "low", "out_of_stock")
    assert "available_quantity" in detail
    assert int(detail["available_quantity"]) >= 0
    if detail["stock_status"] == "out_of_stock":
        assert detail["available_quantity"] == 0
    assert "stock_source" not in detail
    assert "stock" not in detail

    future = timezone.now() + timedelta(days=30)
    with tenant_scope_from_store(store=store, reason="test fixture"):
        StorefrontCTA.objects.create(
            store=store,
            cta_text="Scheduled promo",
            is_active=True,
            start_date=future,
        )
    nr = client.get("/api/v1/notifications/active/")
    assert nr.status_code == 200
    notifs = nr.json()
    sched = next(n for n in notifs if n.get("cta_text") == "Scheduled promo")
    assert sched["is_active"] is True
    assert sched["is_currently_active"] is False
    assert sched.get("start_at") is not None
