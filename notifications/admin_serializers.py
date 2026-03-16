from rest_framework import serializers

from .models import Notification


class AdminNotificationSerializer(serializers.ModelSerializer):
    is_currently_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id', 'text', 'notification_type', 'is_active',
            'is_currently_active', 'link', 'link_text',
            'start_date', 'end_date', 'order',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
