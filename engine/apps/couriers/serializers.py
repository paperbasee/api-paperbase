from rest_framework import serializers
from engine.core.serializers import SafeModelSerializer

from engine.core.encryption import decrypt_value, encrypt_value, mask_value

from .models import Courier


class CourierSerializer(SafeModelSerializer):
    """Read serializer — returns masked credentials, never raw values."""

    api_key_masked = serializers.SerializerMethodField()
    secret_key_masked = serializers.SerializerMethodField()

    class Meta:
        model = Courier
        fields = [
            "public_id",
            "provider",
            "is_active",
            "api_key_masked",
            "secret_key_masked",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "created_at", "updated_at"]

    def get_api_key_masked(self, obj: Courier) -> str:
        return mask_value(decrypt_value(obj.api_key_encrypted))

    def get_secret_key_masked(self, obj: Courier) -> str:
        return mask_value(decrypt_value(obj.secret_key_encrypted))


class CourierConnectSerializer(serializers.Serializer):
    """Write serializer for connecting Steadfast."""

    provider = serializers.ChoiceField(
        choices=Courier.Provider.choices,
        required=False,
        default=Courier.Provider.STEADFAST,
    )
    api_key = serializers.CharField(write_only=True)
    secret_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True, default=""
    )
    is_active = serializers.BooleanField(default=True)

    def validate(self, attrs):
        for key in ("api_key", "secret_key"):
            if key in attrs and isinstance(attrs[key], str):
                attrs[key] = attrs[key].strip()
        p = attrs.get("provider", Courier.Provider.STEADFAST)
        if p != Courier.Provider.STEADFAST:
            raise serializers.ValidationError({"provider": "Only Steadfast is supported."})
        attrs["provider"] = Courier.Provider.STEADFAST
        if not attrs.get("secret_key"):
            raise serializers.ValidationError(
                {"secret_key": "Secret key is required for Steadfast."}
            )
        return attrs

    def create(self, validated_data):
        return Courier.objects.create(
            store=validated_data["store"],
            provider=validated_data["provider"],
            api_key_encrypted=encrypt_value(validated_data["api_key"]),
            secret_key_encrypted=encrypt_value(validated_data.get("secret_key", "")),
            is_active=validated_data.get("is_active", True),
        )


class CourierUpdateSerializer(serializers.Serializer):
    """Partial-update serializer for courier credentials and status."""

    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    secret_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)

    def update(self, instance: Courier, validated_data):
        if "api_key" in validated_data:
            instance.api_key_encrypted = encrypt_value(validated_data["api_key"])
        if "secret_key" in validated_data:
            instance.secret_key_encrypted = encrypt_value(validated_data["secret_key"])
        if "is_active" in validated_data:
            instance.is_active = validated_data["is_active"]
        instance.save()
        return instance
