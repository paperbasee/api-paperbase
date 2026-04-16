import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from engine.apps.products.models import Category, Product
from engine.core.middleware.tenant_context_middleware import TenantContextMiddleware
from engine.core.tenant_context import _clear_tenant_context
from engine.apps.stores.models import Store, StoreMembership
from engine.apps.stores.services import allocate_unique_store_code
from engine.core.authz import can_enable_internal_override
from engine.core.migration_safety import TenantSafeMigration
from engine.core.tenant_context import TenantContextMissingError
from engine.core.tenant_execution import system_scope, tenant_scope_from_store
from engine.core.tenant_guard import TenantViolationError

User = get_user_model()


def _create_store() -> Store:
    owner = User.objects.create_user(
        email="owner+zerotrust@example.com",
        password="pass1234",
        is_verified=True,
    )
    store = Store.objects.create(
        owner=owner,
        name="Zero Trust Store",
        code=allocate_unique_store_code("ZEROTRUST"),
        owner_name="Owner",
        owner_email="owner+zerotrust@example.com",
        is_active=True,
    )
    StoreMembership.objects.create(
        user=owner,
        store=store,
        role=StoreMembership.Role.OWNER,
        is_active=True,
    )
    return store


@pytest.mark.django_db
def test_superuser_middleware_allows_unscoped_query(settings):
    settings.TENANT_GUARD_STRICT_DEV = True
    user = User.objects.create_superuser(
        email="platform-admin@example.com",
        password="secret123",
    )
    request = RequestFactory().get("/secure-signin/products/category/")
    request.user = user
    mw = TenantContextMiddleware(lambda req: None)
    try:
        mw.process_request(request)
        assert request.context.is_platform_admin is True
        assert request.context.tenant is None
        assert Category.objects.exists() is False
    finally:
        _clear_tenant_context()


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
        category = Category.objects.create(store=store, name="Cat", slug="")
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
        is_superuser=True,
        is_verified=True,
    )
    # Superuser alone is not enough: IP must be on the allowlist.
    assert can_enable_internal_override(user=user, client_ip="10.0.0.1") is False


@pytest.mark.django_db
def test_tenant_guard_violations_raise(settings):
    settings.TENANT_GUARD_STRICT_DEV = True
    store = _create_store()
    with tenant_scope_from_store(store=store, reason="guard_setup"):
        category = Category.objects.create(store=store, name="Tops", slug="")
        Product.objects.create(
            store=store,
            name="Shirt",
            slug="shirt",
            price="30.00",
            category=category,
            stock=10,
        )

    request = RequestFactory().get("/api/v1/products/")
    with pytest.raises(TenantViolationError):
        # Accessing store-scoped objects without a proven tenant context should fail.
        Product.objects.filter(store_id=store.id).exists()

    with system_scope(reason="system_scope_check"):
        assert Store.objects.filter(public_id=store.public_id).exists() is True
