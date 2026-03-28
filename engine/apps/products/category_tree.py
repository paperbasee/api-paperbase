"""
Category tree invariants and query helpers (max depth, no cycles, tenant-safe subtrees).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError

if TYPE_CHECKING:
    from .models import Category

MAX_CATEGORY_DEPTH = 5


def depth_from_root(category: Category) -> int:
    """1-based depth: root = 1."""
    depth = 1
    current: Category | None = category
    seen: set[int] = set()
    hops = 0
    while current is not None and current.parent_id and hops < 32:
        if current.pk and current.pk in seen:
            raise ValidationError("Category hierarchy has a cycle.")
        if current.pk:
            seen.add(current.pk)
        parent = getattr(current, "parent", None)
        if parent is None:
            from .models import Category as CategoryModel

            parent = (
                CategoryModel.objects.filter(pk=current.parent_id)
                .only("id", "parent_id", "store_id")
                .first()
            )
        if parent is None:
            break
        depth += 1
        current = parent
        hops += 1
    return depth


def validate_category_parent(
    *,
    instance_pk: int | None,
    store_id: int,
    parent: Category | None,
) -> None:
    """Raise ValidationError if parent assignment is invalid."""
    if parent is None:
        return
    if parent.store_id != store_id:
        raise ValidationError(
            {"parent": "Parent category must belong to the same store."}
        )
    if instance_pk is not None and parent.pk == instance_pk:
        raise ValidationError({"parent": "A category cannot be its own parent."})
    if instance_pk is not None:
        walk: Category | None = parent
        seen: set[int] = set()
        hops = 0
        while walk is not None and walk.parent_id and hops < 32:
            if walk.pk == instance_pk:
                raise ValidationError(
                    {"parent": "Cannot set parent: would create a cycle."}
                )
            if walk.pk and walk.pk in seen:
                raise ValidationError({"parent": "Category hierarchy has a cycle."})
            if walk.pk:
                seen.add(walk.pk)
            nxt = getattr(walk, "parent", None)
            if nxt is None:
                from .models import Category as CategoryModel

                nxt = (
                    CategoryModel.objects.filter(pk=walk.parent_id)
                    .only("id", "parent_id", "store_id")
                    .first()
                )
            walk = nxt
            hops += 1
    parent_depth = depth_from_root(parent)
    if parent_depth + 1 > MAX_CATEGORY_DEPTH:
        raise ValidationError(
            {
                "parent": (
                    f"Maximum category depth is {MAX_CATEGORY_DEPTH} levels "
                    "(including the new category)."
                )
            }
        )


def descendant_category_pks_including_self(*, store_id: int, root_pk: int) -> list[int]:
    """All descendant primary keys under root (same store), including root."""
    from .models import Category

    ids: list[int] = [root_pk]
    frontier: list[int] = [root_pk]
    for _ in range(MAX_CATEGORY_DEPTH):
        children = list(
            Category.objects.filter(store_id=store_id, parent_id__in=frontier).values_list(
                "pk", flat=True
            )
        )
        if not children:
            break
        ids.extend(children)
        frontier = children
    return ids


def descendant_public_ids_including_self(*, store_id: int, root_pk: int) -> list[str]:
    from .models import Category

    pks = descendant_category_pks_including_self(store_id=store_id, root_pk=root_pk)
    return list(
        Category.objects.filter(pk__in=pks, store_id=store_id).values_list(
            "public_id", flat=True
        )
    )


def excluded_parent_pks_for_category(instance: Category) -> set[int]:
    """Categories that cannot be chosen as parent (self + descendants)."""
    from .models import Category

    if not instance.pk:
        return set()
    own = {instance.pk}
    frontier = [instance.pk]
    for _ in range(MAX_CATEGORY_DEPTH):
        children = list(
            Category.objects.filter(
                store_id=instance.store_id, parent_id__in=frontier
            ).values_list("pk", flat=True)
        )
        if not children:
            break
        own.update(children)
        frontier = children
    return own
