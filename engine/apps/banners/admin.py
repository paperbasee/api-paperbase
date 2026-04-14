from django.contrib import admin

from engine.core.admin import StoreListFilter, StoreScopedAdminMixin

from .models import Banner


@admin.register(Banner)
class BannerAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "public_id",
        "store",
        "title",
        "order",
        "is_active",
        "placement_slots",
        "start_at",
        "end_at",
        "created_at",
    )
    list_filter = (StoreListFilter, "is_active")
    search_fields = ("public_id", "title", "cta_text")
    ordering = ("store", "order", "-created_at")

    def optimize_store_queryset(self, qs):
        return qs.select_related("store")
