from django.contrib import admin

from engine.core.admin import StoreListFilter, StoreScopedAdminMixin

from .models import PlatformNotification, StaffNotification, StorefrontCTA


@admin.register(StaffNotification)
class StaffNotificationAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = ["title", "message_type", "store", "user", "is_read", "created_at"]
    list_filter = [StoreListFilter, "message_type", "is_read"]
    list_editable = ["is_read"]
    readonly_fields = ["public_id", "created_at"]
    search_fields = ["title", "public_id"]

    def optimize_store_queryset(self, qs):
        return qs.select_related("store", "user")


@admin.register(PlatformNotification)
class PlatformNotificationAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "is_active",
        "priority",
        "start_at",
        "end_at",
        "public_id",
        "created_at",
    ]
    list_filter = ["is_active", "created_at"]
    search_fields = ["title", "message", "public_id"]
    readonly_fields = ["public_id", "created_at", "updated_at"]
    fieldsets = (
        (
            "Content",
            {
                "fields": ("title", "message", "is_active", "priority", "daily_limit"),
            },
        ),
        (
            "Call to action (optional)",
            {
                "fields": ("cta_text", "cta_url"),
                "classes": ("collapse",),
            },
        ),
        ("Schedule", {"fields": ("start_at", "end_at")}),
        (
            "Identifiers",
            {
                "fields": ("public_id", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(StorefrontCTA)
class StorefrontCTAAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = [
        "cta_text",
        "store",
        "notification_type",
        "is_active",
        "order",
        "start_date",
        "end_date",
        "created_at",
    ]
    list_filter = [StoreListFilter, "notification_type", "is_active", "created_at"]
    search_fields = ["cta_text", "public_id"]
    autocomplete_fields = ["store"]
    fieldsets = (
        (
            "Store",
            {"fields": ("store",)},
        ),
        ("Content", {"fields": ("cta_text", "notification_type", "is_active", "order")}),
        (
            "Link (Optional)",
            {"fields": ("link", "link_text"), "classes": ("collapse",)},
        ),
        (
            "Scheduling (Optional)",
            {"fields": ("start_date", "end_date"), "classes": ("collapse",)},
        ),
    )

    def optimize_store_queryset(self, qs):
        return qs.select_related("store")
