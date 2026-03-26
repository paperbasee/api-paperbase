import base64

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import serializers

from engine.apps.emails.constants import EMAIL_VERIFICATION, PASSWORD_RESET
from engine.apps.emails.tasks import send_email_task
from engine.apps.stores.models import StoreMembership
from engine.apps.stores.services import store_primary_domain_host
from .services import send_verification_email
from .two_factor_service import disable_2fa

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid_for(user):
    return urlsafe_base64_encode(force_bytes(user.pk))


def _user_from_uid(uid):
    try:
        pk = force_str(urlsafe_base64_decode(uid))
        return User.objects.get(pk=pk)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        return None


def _user_eligible_for_public_password_reset(email: str):
    """
    Public password reset eligibility: any active non-staff/non-superuser user.
    Returns None if no such user (silent — used for unauthenticated reset).
    """
    return (
        User.objects.filter(
            email__iexact=email.strip().lower(),
            is_active=True,
            is_superuser=False,
            is_staff=False,
        )
        .distinct()
        .first()
    )


def _send_verification_email(user, request=None):
    send_verification_email(user)


def _send_password_reset_email(user):
    uid = _uid_for(user)
    token = default_token_generator.make_token(user)
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    link = f"{frontend_url}/auth/password-reset/confirm?uid={uid}&token={token}"
    send_email_task.delay(
        PASSWORD_RESET,
        user.email,
        {
            "user_name": user.get_short_name() or user.email,
            "user_email": user.email,
            "reset_link": link,
        },
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class RegisterSerializer(serializers.Serializer):
    """Creates a new user account with email + password. Sends a verification email."""

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
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")

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
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")
        user = User.objects.create_user(
            password=password,
            is_active=False,
            is_verified=False,
            **validated_data,
        )
        _send_verification_email(user)
        return user


# ---------------------------------------------------------------------------
# Me / Profile
# ---------------------------------------------------------------------------

class StoreSummarySerializer(serializers.ModelSerializer):
    role = serializers.CharField(source="get_role_display")
    store_public_id = serializers.CharField(source="store.public_id", read_only=True)

    class Meta:
        model = StoreMembership
        fields = ["store_public_id", "role"]


class MeSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    stores = serializers.SerializerMethodField()
    active_store_public_id = serializers.SerializerMethodField()
    subscription = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "public_id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone",
            "avatar",
            "is_verified",
            "is_staff",
            "is_superuser",
            "date_joined",
            "active_store_public_id",
            "subscription",
            "stores",
        ]
        read_only_fields = [
            "public_id",
            "email",
            "is_verified",
            "is_staff",
            "is_superuser",
            "date_joined",
        ]

    def get_active_store_public_id(self, obj):
        request = self.context.get("request")
        if request and getattr(request, "auth", None):
            return request.auth.get("active_store_public_id")
        return None

    def get_subscription(self, obj):
        from engine.apps.billing.services import get_active_subscription
        sub = get_active_subscription(obj)
        if not sub:
            return {"active": False, "plan": None, "end_date": None}
        return {
            "active": True,
            "plan": sub.plan.name,
            "end_date": sub.end_date.isoformat(),
        }

    def get_stores(self, obj):
        memberships = StoreMembership.objects.select_related("store").filter(
            user=obj,
            is_active=True,
        )
        return [
            {
                "public_id": m.store.public_id,
                "name": m.store.name,
                "domain": store_primary_domain_host(m.store),
                "role": m.get_role_display(),
            }
            for m in memberships
        ]


# ---------------------------------------------------------------------------
# Password change (authenticated)
# ---------------------------------------------------------------------------

class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(
        required=True, write_only=True, style={"input_type": "password"}
    )
    new_password = serializers.CharField(
        required=True, write_only=True, min_length=8, style={"input_type": "password"}
    )
    new_password_confirm = serializers.CharField(
        required=True, write_only=True, style={"input_type": "password"}
    )

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password_confirm": "Passwords do not match."}
            )
        try:
            validate_password(attrs["new_password"], self.context["request"].user)
        except DjangoValidationError as e:
            raise serializers.ValidationError({"new_password": list(e.messages)})
        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        return user


# ---------------------------------------------------------------------------
# Password reset (unauthenticated — two steps)
# ---------------------------------------------------------------------------

class PasswordResetSerializer(serializers.Serializer):
    """Step 1: accepts an email and sends a reset link. Always returns 200 to prevent enumeration."""

    email = serializers.EmailField(required=True)

    def save(self, **kwargs):
        email = self.validated_data["email"].strip().lower()
        user = _user_eligible_for_public_password_reset(email)
        if user is not None:
            _send_password_reset_email(user)


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Step 2: validates uid + token and sets the new password."""

    uid = serializers.CharField(required=True)
    token = serializers.CharField(required=True)
    new_password = serializers.CharField(
        required=True, write_only=True, min_length=8, style={"input_type": "password"}
    )
    new_password_confirm = serializers.CharField(
        required=True, write_only=True, style={"input_type": "password"}
    )

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError(
                {"new_password_confirm": "Passwords do not match."}
            )

        user = _user_from_uid(attrs["uid"])
        if user is None or not user.is_active:
            raise serializers.ValidationError({"uid": "Invalid reset link."})

        if not default_token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError({"token": "Invalid or expired reset token."})

        try:
            validate_password(attrs["new_password"], user)
        except DjangoValidationError as e:
            raise serializers.ValidationError({"new_password": list(e.messages)})

        attrs["_user"] = user
        return attrs

    def save(self, **kwargs):
        user = self.validated_data["_user"]
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password", "updated_at"])
        return user


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

class EmailVerificationSerializer(serializers.Serializer):
    """Validates a uid + token emailed during registration and marks the account verified."""

    uid = serializers.CharField(required=True)
    token = serializers.CharField(required=True)

    def validate(self, attrs):
        user = _user_from_uid(attrs["uid"])
        if user is None:
            raise serializers.ValidationError({"uid": "Invalid verification link."})

        if not default_token_generator.check_token(user, attrs["token"]):
            raise serializers.ValidationError({"token": "Invalid or expired verification token."})

        if user.is_verified:
            raise serializers.ValidationError("Email is already verified.")

        attrs["_user"] = user
        return attrs

    def save(self, **kwargs):
        user = self.validated_data["_user"]
        user.is_verified = True
        user.is_active = True
        user.save(update_fields=["is_verified", "is_active", "updated_at"])
        return user


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        return (value or "").strip().lower()


class OTPCodeSerializer(serializers.Serializer):
    code = serializers.CharField(required=True, min_length=6, max_length=8)


class TwoFactorDisableSerializer(serializers.Serializer):
    password = serializers.CharField(
        required=True,
        write_only=True,
        style={"input_type": "password"},
    )
    code = serializers.CharField(required=True, min_length=6, max_length=8)

    def validate_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def save(self, **kwargs):
        user = self.context["request"].user
        ok, err = disable_2fa(user, self.validated_data["code"])
        if not ok:
            raise serializers.ValidationError({"code": err})
        return {"disabled": True}


class TwoFactorChallengeVerifySerializer(OTPCodeSerializer):
    challenge_public_id = serializers.CharField(required=True)


class TwoFactorRecoveryVerifySerializer(serializers.Serializer):
    code = serializers.CharField(required=True, max_length=64)

    def validate_code(self, value):
        normalized = "".join((value or "").split()).upper()
        if len(normalized) != 8 or not all(c in "0123456789ABCDEF" for c in normalized):
            raise serializers.ValidationError("Enter the 8-character recovery code.")
        return normalized
