import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tests.core.test_core import _make_store


User = get_user_model()


@pytest.mark.django_db
def test_admin_basic_analytics_overview_requires_tenant_for_superuser():
    store = _make_store("Tenant Required", "tenant-required.local")
    su = User.objects.create_superuser(email="su-basic-analytics@example.com", password="pass1234")
    client = APIClient()
    client.force_authenticate(user=su)

    missing = client.get("/api/v1/admin/basic-analytics/overview/")
    assert missing.status_code == 400
    assert missing.data.get("detail") == "Tenant (store) context is required"

    ok = client.get(
        "/api/v1/admin/basic-analytics/overview/",
        HTTP_X_STORE_PUBLIC_ID=store.public_id,
    )
    assert ok.status_code == 200
    assert "summary" in ok.data
    assert "series" in ok.data
    assert "meta" in ok.data


@pytest.mark.django_db
def test_admin_notifications_summary_requires_tenant_for_superuser():
    store = _make_store("Tenant Required 2", "tenant-required-2.local")
    su = User.objects.create_superuser(email="su-notifs@example.com", password="pass1234")
    client = APIClient()
    client.force_authenticate(user=su)

    missing = client.get("/api/v1/admin/notifications/summary/")
    assert missing.status_code == 400
    assert missing.data.get("detail") == "Tenant (store) context is required"

    ok = client.get(
        "/api/v1/admin/notifications/summary/",
        HTTP_X_STORE_PUBLIC_ID=store.public_id,
    )
    assert ok.status_code == 200
    assert "new_orders_count" in ok.data
    assert "pending_tickets_count" in ok.data
    assert "recent_orders" in ok.data
    assert "recent_tickets" in ok.data
