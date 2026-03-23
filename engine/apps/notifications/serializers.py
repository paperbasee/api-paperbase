from rest_framework import serializers

from .models import PlatformNotification, StorefrontCTA


class NotificationSerializer(serializers.ModelSerializer):
    """Notification serializer for API responses."""
    text = serializers.CharField(source="cta_text", read_only=True)
    isCurrentlyActive = serializers.BooleanField(source='is_currently_active', read_only=True)
    notificationType = serializers.CharField(source='notification_type', read_only=True)

    class Meta:
        model = StorefrontCTA
        fields = [
            'public_id', 'text', 'notificationType', 'isCurrentlyActive',
            'link', 'link_text', 'order', 'created_at',
        ]


class ActiveSystemNotificationSerializer(serializers.ModelSerializer):
    """Read-only global banner payload; never exposes internal PK."""

    class Meta:
        model = PlatformNotification
        fields = ("public_id", "title", "message", "cta_text", "cta_url")
