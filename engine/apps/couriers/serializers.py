from rest_framework import serializers

from engine.core.encryption import decrypt_value, encrypt_value, mask_value

from .models import Courier


class CourierSerializer(serializers.ModelSerializer):
    """Read serializer — returns masked credentials, never raw values."""

    api_key_masked = serializers.SerializerMethodField()
    secret_key_masked = serializers.SerializerMethodField()
    access_token_masked = serializers.SerializerMethodField()

    class Meta:
        model = Courier
        fields = [
            "public_id",
            "provider",
            "is_active",
            "api_key_masked",
            "secret_key_masked",
            "access_token_masked",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "created_at", "updated_at"]

    def get_api_key_masked(self, obj: Courier) -> str:
        return mask_value(decrypt_value(obj.api_key_encrypted))

    def get_secret_key_masked(self, obj: Courier) -> str:
        return mask_value(decrypt_value(obj.secret_key_encrypted))

    def get_access_token_masked(self, obj: Courier) -> str:
        return mask_value(decrypt_value(obj.access_token_encrypted))


class CourierConnectSerializer(serializers.Serializer):
    """Write serializer for connecting a new courier provider."""

    provider = serializers.ChoiceField(choices=Courier.Provider.choices)
    api_key = serializers.CharField(write_only=True)
    secret_key = serializers.CharField(write_only=True, required=False, default="")
    access_token = serializers.CharField(write_only=True, required=False, default="")
    refresh_token = serializers.CharField(write_only=True, required=False, default="")
    is_active = serializers.BooleanField(default=True)

    def validate(self, attrs):
        provider = attrs.get("provider")
        if provider == Courier.Provider.STEADFAST:
            if not attrs.get("secret_key"):
                raise serializers.ValidationError(
                    {"secret_key": "Secret key is required for Steadfast."}
                )
        if provider == Courier.Provider.PATHAO:
            if not attrs.get("access_token"):
                raise serializers.ValidationError(
                    {"access_token": "Access token is required for Pathao."}
                )
        return attrs

    def create(self, validated_data):
        return Courier.objects.create(
            store=validated_data["store"],
            provider=validated_data["provider"],
            api_key_encrypted=encrypt_value(validated_data["api_key"]),
            secret_key_encrypted=encrypt_value(validated_data.get("secret_key", "")),
            access_token_encrypted=encrypt_value(validated_data.get("access_token", "")),
            refresh_token=validated_data.get("refresh_token", ""),
            is_active=validated_data.get("is_active", True),
        )


class CourierUpdateSerializer(serializers.Serializer):
    """Partial-update serializer for courier credentials and status."""

    api_key = serializers.CharField(write_only=True, required=False)
    secret_key = serializers.CharField(write_only=True, required=False)
    access_token = serializers.CharField(write_only=True, required=False)
    refresh_token = serializers.CharField(write_only=True, required=False)
    is_active = serializers.BooleanField(required=False)

    def update(self, instance: Courier, validated_data):
        if "api_key" in validated_data:
            instance.api_key_encrypted = encrypt_value(validated_data["api_key"])
        if "secret_key" in validated_data:
            instance.secret_key_encrypted = encrypt_value(validated_data["secret_key"])
        if "access_token" in validated_data:
            instance.access_token_encrypted = encrypt_value(validated_data["access_token"])
        if "refresh_token" in validated_data:
            instance.refresh_token = validated_data["refresh_token"]
        if "is_active" in validated_data:
            instance.is_active = validated_data["is_active"]
        instance.save()
        return instance
