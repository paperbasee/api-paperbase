from django.contrib import admin

from .models import Banner


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = (
        "public_id",
        "store",
        "title",
        "placement",
        "position",
        "is_clickable",
        "is_active",
        "start_date",
        "end_date",
        "created_at",
    )
    list_filter = ("store", "placement", "is_clickable", "is_active")
    search_fields = ("public_id", "title", "description", "cta_text", "placement")
    ordering = ("store", "placement", "position", "-created_at")
