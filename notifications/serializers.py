from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    """Notification serializer for API responses."""
    isCurrentlyActive = serializers.BooleanField(source='is_currently_active', read_only=True)
    notificationType = serializers.CharField(source='notification_type', read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id', 'text', 'notificationType', 'isCurrentlyActive',
            'link', 'link_text', 'order', 'created_at',
        ]
