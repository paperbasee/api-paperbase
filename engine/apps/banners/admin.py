from django.contrib import admin

from .models import Banner


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
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
    list_filter = ("store", "is_active")
    search_fields = ("public_id", "title", "cta_text")
    ordering = ("store", "order", "-created_at")
