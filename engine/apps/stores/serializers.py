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
            "public_id",
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
        read_only_fields = ["public_id", "created_at", "updated_at", "settings"]


class StoreMembershipSerializer(serializers.ModelSerializer):
    user_public_id = serializers.CharField(source="user.public_id", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    store_public_id = serializers.CharField(source="store.public_id", read_only=True)
    store_name = serializers.CharField(source="store.name", read_only=True)

    class Meta:
        model = StoreMembership
        fields = [
            "public_id",
            "user_public_id",
            "user_email",
            "store_public_id",
            "store_name",
            "role",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["public_id", "user_public_id", "created_at", "user_email", "store_name", "store_public_id"]


class DeleteStoreRequestSerializer(serializers.Serializer):
    """
    Payload for destructive store deletion.

    We intentionally do NOT trim whitespace so backend validations can enforce
    exact-match behavior that aligns with the frontend safeguards.
    """

    account_email = serializers.CharField(required=True, trim_whitespace=False)
    store_name = serializers.CharField(required=True, trim_whitespace=False)

    def validate(self, attrs):
        account_email = attrs["account_email"]
        store_name = attrs["store_name"]

        if not account_email or not account_email.strip():
            raise serializers.ValidationError({"account_email": "account_email is required."})
        if not store_name or not store_name.strip():
            raise serializers.ValidationError({"store_name": "store_name is required."})

        return attrs

