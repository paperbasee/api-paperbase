from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import ActivityLog

User = get_user_model()


class ActivityActorSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ["public_id", "email", "full_name"]


class AdminActivityLogSerializer(serializers.ModelSerializer):
    actor = ActivityActorSerializer(read_only=True)

    class Meta:
        model = ActivityLog
        fields = [
            "id",
            "created_at",
            "actor",
            "action",
            "entity_type",
            "entity_id",
            "summary",
            "metadata",
        ]
