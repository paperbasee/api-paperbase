from django.contrib import admin

from engine.core.admin import StoreListFilter, StoreScopedAdminMixin

from .models import Blog, BlogTag


@admin.register(Blog)
class BlogAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "public_id",
        "store",
        "title",
        "is_featured",
        "is_public",
        "is_deleted",
        "published_at",
        "created_at",
    )
    list_filter = (StoreListFilter, "is_featured", "is_public", "is_deleted")
    search_fields = ("public_id", "title", "slug", "excerpt")
    ordering = ("store", "-created_at")

    def optimize_store_queryset(self, qs):
        return qs.select_related("store", "author").prefetch_related("tags")


@admin.register(BlogTag)
class BlogTagAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = ("public_id", "store", "name", "slug", "created_at")
    list_filter = (StoreListFilter,)
    search_fields = ("public_id", "name", "slug")
    ordering = ("store", "name")

    def optimize_store_queryset(self, qs):
        return qs.select_related("store")
