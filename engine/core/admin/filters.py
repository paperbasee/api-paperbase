from __future__ import annotations

from django.contrib.admin import SimpleListFilter

from engine.apps.stores.models import Store
from engine.core.admin.mixins import accessible_store_ids


class StoreListFilter(SimpleListFilter):
    """
    Right-sidebar "Filter by Store" with consistent title and tenant-scoped lookups
    for non-superusers.

    Set ``store_field_lookup`` on the ModelAdmin or subclass and set ``store_lookup``
    to the ORM path for the Store PK (e.g. ``store_id``, ``order__store_id``).
    """

    title = "Filter by Store"
    parameter_name = "store"
    store_lookup: str = "store_id"

    def lookups(self, request, model_admin):
        qs = Store.objects.all().order_by("name")
        if not getattr(request.user, "is_superuser", False):
            ids = accessible_store_ids(request.user)
            if not ids:
                return []
            qs = qs.filter(pk__in=ids)
        return [(str(s.pk), s.name) for s in qs]

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        try:
            v = int(self.value())
        except (TypeError, ValueError):
            return queryset.none()
        return queryset.filter(**{self.store_lookup: v})


class StoreListFilterByProductStore(StoreListFilter):
    store_lookup = "product__store_id"


class StoreListFilterByInventoryProductStore(StoreListFilter):
    store_lookup = "inventory__product__store_id"


class StoreListFilterByTicketStore(StoreListFilter):
    store_lookup = "ticket__store_id"
