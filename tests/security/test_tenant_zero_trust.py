import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from engine.apps.products.models import Category, Product
from engine.apps.stores.models import Store
from engine.apps.marketing_integrations.services import dispatcher
from engine.core.authz import can_enable_internal_override
from engine.core.migration_safety import TenantSafeMigration
from engine.core.tenant_context import TenantContextMissingError
from engine.core.tenant_execution import system_scope, tenant_scope_from_store
from engine.core.tenant_guard import TenantViolationError

User = get_user_model()


def _create_store() -> Store:
    return Store.objects.create(
        name="Zero Trust Store",
        owner_name="Owner",
        owner_email="owner+zerotrust@example.com",
        is_active=True,
    )


@pytest.mark.django_db
def test_unscoped_query_detection_fails(settings):
    settings.TENANT_GUARD_STRICT_DEV = True
    with pytest.raises(TenantContextMissingError):
        Product.objects.count()


@pytest.mark.django_db
def test_missing_context_causes_exception(settings):
    settings.TENANT_GUARD_STRICT_DEV = True
    with pytest.raises(TenantContextMissingError):
        Category.objects.exists()


@pytest.mark.django_db
def test_background_task_without_context_fails(settings):
    settings.TENANT_GUARD_STRICT_DEV = True
    store = _create_store()
    with tenant_scope_from_store(store=store, reason="seed_for_background_test"):
        category = Category.objects.create(store=store, name="Cat", slug="cat")
        Product.objects.create(
            store=store,
            name="Item",
            slug="item",
            price="10.00",
            category=category,
            stock=1,
        )

    with pytest.raises(TenantContextMissingError):
        Product.objects.filter(store_id=store.id).delete()


@pytest.mark.django_db
def test_migration_without_explicit_scope_fails():
    with pytest.raises(RuntimeError):
        TenantSafeMigration.assert_write_scope(scope="invalid")


@pytest.mark.django_db
def test_migration_system_scope_rejects_active_tenant_context():
    store = _create_store()
    with tenant_scope_from_store(store=store, reason="migration_scope_test"):
        with pytest.raises(RuntimeError):
            TenantSafeMigration.assert_write_scope(scope=TenantSafeMigration.SYSTEM_SCOPE)


@pytest.mark.django_db
def test_auth_bypass_via_staff_without_auth_context_fails(settings):
    settings.SECURITY_INTERNAL_OVERRIDE_ALLOWED = True
    settings.INTERNAL_OVERRIDE_IP_ALLOWLIST = ["127.0.0.1"]
    user = User.objects.create_user(
        email="staff-bypass@example.com",
        password="secret123",
        is_staff=True,
        is_verified=True,
    )
    # Staff alone is not enough: IP must be on the allowlist.
    assert can_enable_internal_override(user=user, client_ip="10.0.0.1") is False


@pytest.mark.django_db
def test_dispatcher_cannot_infer_tenant_from_objects(settings):
    settings.TENANT_GUARD_STRICT_DEV = True
    store = _create_store()
    with tenant_scope_from_store(store=store, reason="dispatcher_setup"):
        category = Category.objects.create(store=store, name="Tops", slug="tops")
        product = Product.objects.create(
            store=store,
            name="Shirt",
            slug="shirt",
            price="30.00",
            category=category,
            stock=10,
        )

    request = RequestFactory().get("/api/v1/products/")
    with pytest.raises(TenantViolationError):
        dispatcher.track_view_content(request, product)

    with system_scope(reason="dispatcher_system_scope_check"):
        # Explicit system scope allows execution context, but no dispatch without integrations.
        assert dispatcher._resolve_store(store=None) is None
