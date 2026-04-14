from django.contrib import admin

from engine.core.admin import StoreListFilter, StoreScopedAdminMixin

from .models import EmailLog, EmailTemplate


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("type", "subject", "is_active", "public_id", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("type", "subject")
    readonly_fields = ("public_id", "created_at", "updated_at")


@admin.register(EmailLog)
class EmailLogAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    tenant_store_lookup = "store_id"

    list_display = (
        "public_id",
        "store",
        "to_email",
        "type",
        "status",
        "provider",
        "sent_at",
        "created_at",
    )
    list_filter = (StoreListFilter, "status", "type", "provider")
    search_fields = ("to_email", "type", "public_id")
    readonly_fields = (
        "public_id",
        "to_email",
        "type",
        "status",
        "provider",
        "error_message",
        "metadata",
        "sent_at",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if not getattr(request.user, "is_superuser", False):
            fields = [f for f in fields if f != "metadata"]
        return fields
