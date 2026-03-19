from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Store, StoreSettings, StoreMembership

User = get_user_model()


class StoreSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreSettings
        fields = [
            "modules_enabled",
            "low_stock_threshold",
            "extra_field_schema",
        ]


class StoreSerializer(serializers.ModelSerializer):
    settings = StoreSettingsSerializer(read_only=True)

    class Meta:
        model = Store
        fields = [
            "id",
            "name",
            "store_type",
            "domain",
            "owner_name",
            "owner_email",
            "is_active",
            "currency",
            "created_at",
            "updated_at",
            "settings",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "settings"]


class StoreMembershipSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)
    store_name = serializers.CharField(source="store.name", read_only=True)

    class Meta:
        model = StoreMembership
        fields = [
            "id",
            "user",
            "user_email",
            "user_username",
            "store",
            "store_name",
            "role",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "user_email", "user_username", "store_name"]

