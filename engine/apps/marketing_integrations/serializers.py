from rest_framework import serializers
from engine.core.serializers import SafeModelSerializer

from engine.core.encryption import decrypt_value, encrypt_value, mask_value

from .models import IntegrationEventSettings, MarketingIntegration


class IntegrationEventSettingsSerializer(SafeModelSerializer):
    class Meta:
        model = IntegrationEventSettings
        fields = [
            "track_purchase",
            "track_initiate_checkout",
            "track_add_to_cart",
            "track_view_content",
        ]


class MarketingIntegrationSerializer(SafeModelSerializer):
    """Read serializer — returns masked credentials, never raw values."""

    access_token_masked = serializers.SerializerMethodField()
    event_settings = IntegrationEventSettingsSerializer(read_only=True)

    class Meta:
        model = MarketingIntegration
        fields = [
            "public_id",
            "provider",
            "pixel_id",
            "access_token_masked",
            "test_event_code",
            "is_active",
            "event_settings",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "created_at", "updated_at"]

    def get_access_token_masked(self, obj: MarketingIntegration) -> str:
        return mask_value(decrypt_value(obj.access_token_encrypted))


class MarketingIntegrationConnectSerializer(serializers.Serializer):
    """Write serializer for connecting a new marketing integration."""

    provider = serializers.ChoiceField(choices=MarketingIntegration.Provider.choices)
    pixel_id = serializers.CharField(required=False, default="", allow_blank=True)
    access_token = serializers.CharField(write_only=True)
    test_event_code = serializers.CharField(required=False, default="", allow_blank=True)
    is_active = serializers.BooleanField(default=True)

    def validate(self, attrs):
        provider = attrs.get("provider")
        if provider == MarketingIntegration.Provider.FACEBOOK:
            if not attrs.get("pixel_id"):
                raise serializers.ValidationError(
                    {"pixel_id": "Pixel ID is required for Facebook."}
                )
            if not attrs.get("access_token"):
                raise serializers.ValidationError(
                    {"access_token": "Access token is required for Facebook."}
                )
        return attrs

    def create(self, validated_data):
        integration = MarketingIntegration.objects.create(
            store=validated_data["store"],
            provider=validated_data["provider"],
            pixel_id=validated_data.get("pixel_id", ""),
            access_token_encrypted=encrypt_value(validated_data["access_token"]),
            test_event_code=validated_data.get("test_event_code", ""),
            is_active=validated_data.get("is_active", True),
        )
        IntegrationEventSettings.objects.create(integration=integration)
        return integration


class MarketingIntegrationUpdateSerializer(serializers.Serializer):
    """Partial-update serializer for marketing integration credentials and status."""

    pixel_id = serializers.CharField(required=False, allow_blank=True)
    access_token = serializers.CharField(write_only=True, required=False)
    test_event_code = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)

    def update(self, instance: MarketingIntegration, validated_data):
        if "pixel_id" in validated_data:
            instance.pixel_id = validated_data["pixel_id"]
        if "access_token" in validated_data:
            instance.access_token_encrypted = encrypt_value(validated_data["access_token"])
        if "test_event_code" in validated_data:
            instance.test_event_code = validated_data["test_event_code"]
        if "is_active" in validated_data:
            instance.is_active = validated_data["is_active"]
        instance.save()
        return instance
