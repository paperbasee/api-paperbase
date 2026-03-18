from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from engine.apps.stores.models import StoreMembership

User = get_user_model()


class RegisterSerializer(serializers.Serializer):
    """Serializer for user registration. Creates regular users (not staff/superuser)."""

    email = serializers.EmailField(required=True, write_only=True)
    password = serializers.CharField(
        required=True,
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
    )
    password_confirm = serializers.CharField(
        required=True,
        write_only=True,
        style={"input_type": "password"},
    )

    def validate_email(self, value):
        value = (value or "").strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": "Passwords do not match."}
            )
        try:
            validate_password(attrs["password"])
        except DjangoValidationError as e:
            raise serializers.ValidationError({"password": list(e.messages)})
        return attrs

    def create(self, validated_data):
        email = validated_data["email"]
        password = validated_data["password"]
        # Use email as username; truncate to 150 chars (Django User.username max_length)
        username = email[:150]
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            is_staff=False,
            is_superuser=False,
        )
        return user


class StoreSummarySerializer(serializers.ModelSerializer):
    role = serializers.CharField(source="get_role_display")

    class Meta:
        model = StoreMembership
        fields = ["store_id", "store", "role"]
        extra_kwargs = {
            "store": {"read_only": True},
        }


class MeSerializer(serializers.ModelSerializer):
    stores = serializers.SerializerMethodField()
    active_store_id = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_staff",
            "is_superuser",
            "active_store_id",
            "stores",
        ]

    def get_active_store_id(self, obj):
        request = self.context.get("request")
        if request and getattr(request, "auth", None):
            return request.auth.get("active_store_id")
        return None

    def get_stores(self, obj):
        memberships = StoreMembership.objects.select_related("store").filter(
            user=obj,
            is_active=True,
        )
        return [
            {
                "id": m.store_id,
                "name": m.store.name,
                "domain": m.store.domain,
                "role": m.get_role_display(),
            }
            for m in memberships
        ]


