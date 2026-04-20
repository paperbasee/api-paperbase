from django.contrib.auth import get_user_model
from rest_framework import serializers
from engine.core.serializers import SafeModelSerializer

from engine.apps.billing.feature_gate import has_feature

from .models import Store, StoreMembership, StoreSettings
from .services import ORDER_EMAIL_NOTIFICATIONS_FEATURE

User = get_user_model()


class StoreSettingsSerializer(SafeModelSerializer):
    class Meta:
        model = StoreSettings
        fields = [
            "modules_enabled",
            "low_stock_threshold",
            "extra_field_schema",
            "email_notify_owner_on_order_received",
            "email_customer_on_order_confirmed",
            "public_api_enabled",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request and getattr(request.user, "is_authenticated", False):
            if not has_feature(request.user, ORDER_EMAIL_NOTIFICATIONS_FEATURE):
                data["email_notify_owner_on_order_received"] = False
                data["email_customer_on_order_confirmed"] = False
        return data

    def validate(self, attrs):
        request = self.context.get("request")
        membership = self.context.get("membership")
        for key in (
            "email_notify_owner_on_order_received",
            "email_customer_on_order_confirmed",
        ):
            if key not in attrs:
                continue
            if (
                not membership
                or membership.role != StoreMembership.Role.OWNER
                or not membership.is_active
            ):
                raise serializers.ValidationError(
                    {key: "Only the store owner can change order email notification settings."}
                )
            if attrs[key] and (
                not request
                or not getattr(request.user, "is_authenticated", False)
                or not has_feature(request.user, ORDER_EMAIL_NOTIFICATIONS_FEATURE)
            ):
                raise serializers.ValidationError(
                    {
                        key: (
                            "This feature (order_email_notifications) is not available on your plan. "
                            "Please upgrade."
                        )
                    }
                )
        return attrs


class StoreSerializer(SafeModelSerializer):
    settings = StoreSettingsSerializer(read_only=True)

    class Meta:
        model = Store
        fields = [
            "public_id",
            "name",
            "store_type",
            "owner_name",
            "owner_email",
            "is_active",
            "status",
            "delete_at",
            "removed_at",
            "currency",
            "created_at",
            "updated_at",
            "settings",
        ]
        read_only_fields = [
            "public_id",
            "created_at",
            "updated_at",
            "settings",
            "status",
            "delete_at",
            "removed_at",
        ]


class StoreMembershipSerializer(SafeModelSerializer):
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
