import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.http import HttpResponse
from django.test import RequestFactory
from rest_framework.test import APIClient

from engine.apps.notifications.models import StaffNotification
from engine.apps.products.models import ProductAttribute
from engine.apps.stores.models import Store, StoreMembership
from engine.apps.stores.services import allocate_unique_store_code, normalize_store_code_base_from_name
from django.test import override_settings

from engine.core.client_ip import get_client_ip
from engine.core.middleware.internal_override_middleware import InternalOverrideMiddleware

User = get_user_model()


def _make_store(name: str) -> Store:
    base = normalize_store_code_base_from_name(name) or "T"
    return Store.objects.create(
        name=name,
        code=allocate_unique_store_code(base),
        owner_name=f"{name} Owner",
        owner_email=f"{name.lower().replace(' ', '')}@example.com",
    )


@pytest.mark.django_db
def test_internal_override_requires_allowlisted_ip(settings):
    settings.SECURITY_INTERNAL_OVERRIDE_ALLOWED = True
    settings.INTERNAL_OVERRIDE_IP_ALLOWLIST = ["127.0.0.1"]
    settings.DEBUG = True
    settings.TESTING = True

    user = User.objects.create_user(
        email="staff-allowlist@example.com",
        password="secret123",
        is_staff=True,
        is_verified=True,
    )

    middleware = InternalOverrideMiddleware(lambda request: HttpResponse(status=200))
    factory = RequestFactory()

    blocked_request = factory.get("/api/v1/products/", REMOTE_ADDR="10.10.10.10")
    blocked_request.user = user
    middleware.process_request(blocked_request)
    assert blocked_request.auth_context.internal_override_enabled is False

    allowed_request = factory.get("/api/v1/products/", REMOTE_ADDR="127.0.0.1")
    allowed_request.user = user
    middleware.process_request(allowed_request)
    assert allowed_request.auth_context.internal_override_enabled is True


def test_get_client_ip_matches_drf_num_proxies_one():
    factory = RequestFactory()
    req = factory.get(
        "/api/v1/products/",
        HTTP_X_FORWARDED_FOR="203.0.113.1, 10.0.0.1",
        REMOTE_ADDR="10.0.0.2",
    )
    with override_settings(
        TRUSTED_IP_HEADER="HTTP_X_FORWARDED_FOR",
        REST_FRAMEWORK={
            "NUM_PROXIES": 1,
            "DEFAULT_THROTTLE_RATES": {},
        },
    ):
        assert get_client_ip(req) == "10.0.0.1"


@pytest.mark.django_db
def test_admin_product_attributes_are_scoped_by_store():
    store_a = _make_store("Store A")
    store_b = _make_store("Store B")

    attr_a = ProductAttribute.objects.create(store=store_a, name="Color", slug="color", order=1)
    attr_b = ProductAttribute.objects.create(store=store_b, name="Fit", slug="fit", order=1)

    staff = User.objects.create_user(
        email="staff-admin@example.com",
        password="secret123",
        is_staff=True,
        is_verified=True,
    )
    StoreMembership.objects.create(
        user=staff,
        store=store_a,
        role=StoreMembership.Role.ADMIN,
        is_active=True,
    )
    client = APIClient()
    client.force_authenticate(user=staff)

    response = client.get(
        "/api/v1/admin/product-attributes/",
        HTTP_X_STORE_PUBLIC_ID=store_a.public_id,
    )

    assert response.status_code == 200
    public_ids = [row["public_id"] for row in response.data.get("results", response.data)]
    assert attr_a.public_id in public_ids
    assert attr_b.public_id not in public_ids


@pytest.mark.django_db
def test_staff_notification_requires_store_and_user():
    with pytest.raises(IntegrityError):
        StaffNotification.objects.create(
            message_type=StaffNotification.MessageType.OTHER,
            title="Missing scope",
            payload={},
        )
