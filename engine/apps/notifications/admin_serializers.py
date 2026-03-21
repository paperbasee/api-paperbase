from rest_framework import serializers

from .models import Notification, StaffInboxNotification


class AdminStaffInboxNotificationSerializer(serializers.ModelSerializer):
    user_public_id = serializers.CharField(source="user.public_id", read_only=True, allow_null=True)

    class Meta:
        model = StaffInboxNotification
        fields = ['public_id', 'user_public_id', 'message_type', 'title', 'payload', 'is_read', 'created_at']
        read_only_fields = ['public_id', 'user_public_id', 'message_type', 'title', 'payload', 'created_at']


class AdminNotificationSerializer(serializers.ModelSerializer):
    is_currently_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = [
            'public_id', 'text', 'notification_type', 'is_active',
            'is_currently_active', 'link', 'link_text',
            'start_date', 'end_date', 'order',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['public_id', 'created_at', 'updated_at']
