from django.contrib import admin

from engine.core.admin import (
    StoreListFilter,
    StoreListFilterByTicketStore,
    StoreScopedAdminMixin,
)

from .models import SupportTicket, SupportTicketAttachment


@admin.register(SupportTicket)
class SupportTicketAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    list_display = ["id", "store", "subject", "name", "email", "status", "priority", "created_at"]
    list_filter = [StoreListFilter, "status", "priority", "category", "created_at"]
    search_fields = ["subject", "name", "email", "phone", "order_number", "message"]
    readonly_fields = ["created_at", "updated_at"]
    autocomplete_fields = ["store"]

    def has_add_permission(self, request):
        return False

    def optimize_store_queryset(self, qs):
        return qs.select_related("store")


@admin.register(SupportTicketAttachment)
class SupportTicketAttachmentAdmin(StoreScopedAdminMixin, admin.ModelAdmin):
    tenant_store_lookup = "ticket__store_id"

    list_display = ["id", "ticket", "store_display", "created_at"]
    list_filter = [StoreListFilterByTicketStore, "created_at"]
    search_fields = ["ticket__id"]
    autocomplete_fields = ["ticket"]

    def optimize_store_queryset(self, qs):
        return qs.select_related("ticket__store")

    @admin.display(description="Store")
    def store_display(self, obj):
        return obj.ticket.store if obj.ticket_id else None
