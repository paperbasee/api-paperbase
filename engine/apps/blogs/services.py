"""Blog CMS service layer — store-scoped CRUD + cache."""

from __future__ import annotations

from django.conf import settings
from django.db.models import F, Q, QuerySet
from django.utils import timezone

from engine.core import cache_service

from .models import Blog


def _public_list_queryset(store) -> QuerySet[Blog]:
    now = timezone.now()
    return (
        Blog.objects.filter(
            store=store,
            is_deleted=False,
            is_public=True,
            published_at__isnull=False,
            published_at__lte=now,
        )
        .select_related("author")
        .prefetch_related("tags")
    )


def get_public_blogs(store, request, *, tag_slug: str | None = None):
    """Cached list of published blogs for the storefront."""
    from .serializers import PublicBlogListSerializer

    cache_key = cache_service.build_key(
        store.public_id,
        "blogs",
        f"list:{tag_slug or 'all'}",
    )

    def fetcher():
        qs = _public_list_queryset(store)
        if tag_slug:
            qs = qs.filter(tags__slug=tag_slug).distinct()
        return PublicBlogListSerializer(qs, many=True, context={"request": request}).data

    return cache_service.get_or_set(cache_key, fetcher, settings.CACHE_TTL_BLOGS)


def get_public_blog_detail(store, public_id: str, request):
    """Return serialized detail for a published storefront blog, or None if missing."""
    from .serializers import PublicBlogDetailSerializer

    blog = (
        _public_list_queryset(store)
        .filter(public_id=public_id)
        .first()
    )
    if blog is None:
        return None
    # Fire-and-forget view counter bump (F-expression, no race).
    Blog.objects.filter(pk=blog.pk).update(views=F("views") + 1)
    blog.views = (blog.views or 0) + 1
    return PublicBlogDetailSerializer(blog, context={"request": request}).data


# --- Dashboard service helpers -----------------------------------------------


def ensure_blog_published(blog: Blog) -> Blog:
    """Set `published_at` when missing so the post is visible on the storefront."""
    if blog.published_at is None:
        blog.published_at = timezone.now()
        blog.save(update_fields=["published_at", "updated_at"])
    return blog


def soft_delete_blog(blog: Blog) -> Blog:
    """Soft-delete a blog and invalidate cache."""
    blog.soft_delete()
    invalidate_blog_cache(blog.store.public_id)
    return blog


def invalidate_blog_cache(store_public_id: str) -> None:
    """Clear all blog cache entries for a store."""
    cache_service.invalidate_store_resource(store_public_id, "blogs")
