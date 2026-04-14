from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import ValidationError

from engine.core.admin import StoreListFilter, StoreScopedAdminMixin

from .models import Store, StoreMembership, StoreSettings


@admin.register(Store)
class StoreAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    tenant_store_lookup = "pk"

    list_display = (
        "id",
        "name",
        "code",
        "owner_name",
        "owner_email",
        "currency_symbol",
        "status",
        "is_active",
        "delete_at",
        "created_at",
    )
    list_filter = ("is_active", "status", "created_at")
    search_fields = ("name", "owner_name", "owner_email")
    ordering = ("-created_at",)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj) or [])
        if obj:
            ro.append("code")
        return tuple(ro)

    fieldsets = (
        (None, {"fields": ("name", "slug", "code", "store_type", "is_active")}),
        ("Owner", {"fields": ("owner_name", "owner_email")}),
        ("Branding", {"fields": ("logo", "currency", "currency_symbol")}),
        ("Store info", {"fields": ("contact_email", "phone", "address")}),
    )

    actions = ["safe_delete_selected"]

    def get_actions(self, request):
        """
        Replace Django's default bulk delete action, which always shows a success
        message based on the original queryset size (even if some deletions fail).
        """
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.action(description="Delete selected stores")
    def safe_delete_selected(self, request, queryset):
        ok = 0
        failed = 0
        for obj in queryset:
            try:
                obj.delete()
                ok += 1
            except ValidationError as exc:
                failed += 1
                msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                messages.error(request, f"{obj}: {msg}")

        if ok:
            messages.success(
                request,
                f"Successfully deleted {ok} store{'s' if ok != 1 else ''}.",
            )
        if failed and not ok:
            messages.warning(
                request,
                f"No stores were deleted. {failed} failed validation.",
            )

    def delete_model(self, request, obj):
        """
        Gracefully surface deletion validation errors in Django admin
        (e.g. missing contact_email), instead of throwing a 500.
        """
        try:
            super().delete_model(request, obj)
        except ValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            messages.error(request, msg)

    def delete_queryset(self, request, queryset):
        """
        Bulk delete from changelist view can trigger model signals that raise
        ValidationError. Handle per-row so the admin UI doesn't crash.
        """
        for obj in queryset:
            try:
                obj.delete()
            except ValidationError as exc:
                msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
                messages.error(request, f"{obj}: {msg}")


@admin.register(StoreSettings)
class StoreSettingsAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = ("store", "low_stock_threshold", "created_at")
    list_filter = (StoreListFilter,)
    search_fields = ("store__name",)

    def optimize_store_queryset(self, qs):
        return qs.select_related("store")


@admin.register(StoreMembership)
class StoreMembershipAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = ("id", "user", "store", "role", "is_active", "created_at")
    list_filter = (StoreListFilter, "role", "is_active", "created_at")
    search_fields = ("user__email", "store__name")
    raw_id_fields = ("user", "store")

    def optimize_store_queryset(self, qs):
        return qs.select_related("user", "store")
