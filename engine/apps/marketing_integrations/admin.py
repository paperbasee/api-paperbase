from django.contrib import admin

from engine.core.admin import StoreListFilter, StoreScopedAdminMixin

from .models import StoreEventLog


@admin.register(StoreEventLog)
class StoreEventLogAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "store",
        "app",
        "event_type",
        "status",
        "created_at",
    )
    list_filter = (StoreListFilter, "app", "event_type", "status", "created_at")
    search_fields = ("store__name", "store__public_id", "event_type", "message")
    ordering = ("-created_at",)
    readonly_fields = ("store", "app", "event_type", "status", "message", "metadata", "created_at")
