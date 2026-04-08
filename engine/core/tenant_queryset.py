from __future__ import annotations

from django.db import models

from engine.core.tenant_context import (
    TenantContextMissingError,
    get_current_store,
    get_is_platform_admin,
)
from engine.core.tenant_execution import in_system_scope
from engine.core.tenant_guard import strict_guard_enabled, validate_tenant_query_allowed


class TenantAwareQuerySet(models.QuerySet):
    def _guard(self, operation: str) -> None:
        validate_tenant_query_allowed(model_name=self.model.__name__, operation=operation)

    def for_store(self, store):
        if store is None:
            raise TenantContextMissingError("Store context is required for tenant query.")
        return self.filter(store=store)

    def for_current_store(self):
        return self.for_store(get_current_store())

    def get(self, *args, **kwargs):
        self._guard("get")
        return super().get(*args, **kwargs)

    def _fetch_all(self):
        self._guard("_fetch_all")
        return super()._fetch_all()

    def __iter__(self):
        self._guard("__iter__")
        return super().__iter__()

    def count(self):
        self._guard("count")
        return super().count()

    def exists(self):
        self._guard("exists")
        return super().exists()

    def first(self):
        self._guard("first")
        return super().first()

    def last(self):
        self._guard("last")
        return super().last()

    def delete(self):
        self._guard("delete")
        return super().delete()

    def update(self, **kwargs):
        self._guard("update")
        return super().update(**kwargs)


class TenantAwareManager(models.Manager):
    """
    Manager that auto-scopes store-aware models to the current tenant context.
    """

    def get_queryset(self):
        qs = TenantAwareQuerySet(self.model, using=self._db)
        store = get_current_store()
        if in_system_scope():
            return qs
        if get_is_platform_admin():
            return qs
        if store is None:
            # No store context at import/collectstatic/admin form build; evaluation still guarded in TenantAwareQuerySet._guard.
            if strict_guard_enabled():
                return qs
            return qs
        return qs.filter(store=store)

    def for_store(self, store):
        return self.get_queryset().for_store(store)
