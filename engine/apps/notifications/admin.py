from django.contrib import admin

from .models import Notification, StaffInboxNotification, SystemNotification


@admin.register(StaffInboxNotification)
class StaffInboxNotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'message_type', 'user', 'is_read', 'created_at']
    list_filter = ['message_type', 'is_read']
    list_editable = ['is_read']
    readonly_fields = ['public_id', 'created_at']
    search_fields = ['title', 'public_id']


@admin.register(SystemNotification)
class SystemNotificationAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'is_active', 'priority', 'start_at', 'end_at', 'public_id', 'created_at',
    ]
    list_filter = ['is_active', 'created_at']
    search_fields = ['title', 'message', 'public_id']
    readonly_fields = ['public_id', 'created_at', 'updated_at']
    fieldsets = (
        ('Content', {
            'fields': ('title', 'message', 'is_active', 'priority'),
        }),
        ('Call to action (optional)', {
            'fields': ('cta_text', 'cta_url'),
            'classes': ('collapse',),
        }),
        ('Schedule', {
            'fields': ('start_at', 'end_at'),
        }),
        ('Identifiers', {
            'fields': ('public_id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['text', 'notification_type', 'is_active', 'order', 'start_date', 'end_date', 'created_at']
    list_filter = ['notification_type', 'is_active', 'created_at']
    search_fields = ['text']
    fieldsets = (
        ('Content', {
            'fields': ('text', 'notification_type', 'is_active', 'order')
        }),
        ('Link (Optional)', {
            'fields': ('link', 'link_text'),
            'classes': ('collapse',)
        }),
        ('Scheduling (Optional)', {
            'fields': ('start_date', 'end_date'),
            'classes': ('collapse',)
        }),
    )
