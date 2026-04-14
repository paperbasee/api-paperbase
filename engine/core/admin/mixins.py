from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


def accessible_store_ids(user: AbstractBaseUser) -> set[int]:
    """
    Store primary keys the user may access in Django admin (owner + active memberships).
    """
    if not getattr(user, "is_authenticated", False):
        return set()
    ids: set[int] = set()
    oid = getattr(user, "owned_store_id", None)
    if oid is not None:
        ids.add(int(oid))
    from engine.apps.stores.models import StoreMembership

    ids.update(
        StoreMembership.objects.filter(user=user, is_active=True).values_list(
            "store_id", flat=True
        )
    )
    return {int(x) for x in ids}


class StoreScopedAdminMixin:
    """
    Restrict changelist/detail queryset to stores the user can access.
    Superusers see all rows. Uses tenant_store_lookup (Django ORM field path ending in _id).
    """

    tenant_store_lookup: str = "store_id"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = self.optimize_store_queryset(qs)
        if getattr(request.user, "is_superuser", False):
            return qs
        ids = accessible_store_ids(request.user)
        if not ids:
            return qs.none()
        return qs.filter(**{f"{self.tenant_store_lookup}__in": ids})

    def optimize_store_queryset(self, qs: models.QuerySet) -> models.QuerySet:
        return qs
