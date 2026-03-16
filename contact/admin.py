from django.contrib import admin

from .models import ContactSubmission


@admin.register(ContactSubmission)
class ContactSubmissionAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'email', 'created_at']
    list_filter = ['created_at']
    search_fields = ['name', 'phone', 'email']
    readonly_fields = ['name', 'phone', 'email', 'message', 'created_at']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
