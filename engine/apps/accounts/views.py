from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.conf import settings
from rest_framework import permissions, views, status
from rest_framework.response import Response
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from engine.apps.billing.feature_gate import get_feature_config
from engine.apps.emails.triggers import queue_two_fa_disabled_email
from engine.apps.stores.models import StoreMembership
from .models import UserTwoFactor
from .two_factor_service import (
    begin_setup,
    create_challenge,
    get_or_create_profile,
    request_recovery_code,
    verify_challenge,
    verify_recovery_and_disable_2fa,
    verify_setup_code,
)

from .serializers import (
    MeSerializer,
    RegisterSerializer,
    PasswordChangeSerializer,
    PasswordResetSerializer,
    PasswordResetConfirmSerializer,
    EmailVerificationSerializer,
    OTPCodeSerializer,
    TwoFactorChallengeVerifySerializer,
    TwoFactorDisableSerializer,
    TwoFactorRecoveryVerifySerializer,
    _send_verification_email,
)
from .throttles import (
    LoginRateThrottle,
    OTPChallengeRateThrottle,
    OTPManageRateThrottle,
    PasswordResetRateThrottle,
    RegisterRateThrottle,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# JWT — store-aware token
# ---------------------------------------------------------------------------

class StoreAwareTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Extend JWT payload with `active_store_public_id` claim (store.public_id).
    Also add `active_store_public_id` to response body for frontend routing.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        membership = (
            StoreMembership.objects.select_related("store")
            .filter(user=user, is_active=True)
            .order_by("created_at")
            .first()
        )
        if membership:
            token["active_store_public_id"] = membership.store.public_id
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        membership = (
            StoreMembership.objects.select_related("store")
            .filter(user=self.user, is_active=True)
            .order_by("created_at")
            .first()
        )
        data["active_store_public_id"] = membership.store.public_id if membership else None
        return data


def _get_first_active_store_public_id(user):
    membership = (
        StoreMembership.objects.select_related("store")
        .filter(user=user, is_active=True)
        .order_by("created_at")
        .first()
    )
    return membership.store.public_id if membership else None


def _issue_tokens(user, store_public_id=None):
    resolved_store_public_id = store_public_id or _get_first_active_store_public_id(user)
    refresh = RefreshToken.for_user(user)
    if resolved_store_public_id:
        refresh["active_store_public_id"] = resolved_store_public_id
    access = refresh.access_token
    if resolved_store_public_id:
        access["active_store_public_id"] = resolved_store_public_id
    return {
        "access": str(access),
        "refresh": str(refresh),
        "active_store_public_id": resolved_store_public_id,
    }


class StoreAwareTokenObtainPairView(views.APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [] if getattr(settings, "TESTING", False) else [LoginRateThrottle]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        if not email or not password:
            return Response({"detail": "Email and password are required."}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(request, username=email, password=password)
        if not user:
            return Response({"detail": "No active account found with the given credentials"}, status=status.HTTP_401_UNAUTHORIZED)

        profile = get_or_create_profile(user)
        if profile.is_enabled:
            challenge = create_challenge(user, flow="login")
            return Response(
                {
                    "2fa_required": True,
                    "challenge_public_id": challenge.challenge_id,
                    "flow": challenge.flow,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        return Response(_issue_tokens(user), status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class RegisterView(views.APIView):
    """
    POST /auth/register/
    Create a new user account. Returns JWT tokens for immediate login.
    A verification email is sent automatically.
    """

    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterRateThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.save()
        profile = get_or_create_profile(user)
        if profile.is_enabled:
            challenge = create_challenge(user, flow="register")
            return Response(
                {
                    "2fa_required": True,
                    "challenge_public_id": challenge.challenge_id,
                    "flow": challenge.flow,
                },
                status=status.HTTP_202_ACCEPTED,
            )
        return Response(_issue_tokens(user), status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Me / Profile
# ---------------------------------------------------------------------------

class MeView(views.APIView):
    """
    GET  /auth/me/  — return authenticated user profile + store memberships
    PATCH /auth/me/ — update first_name, last_name, phone, avatar
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = MeSerializer(request.user, context={"request": request})
        return Response(serializer.data)

    def patch(self, request):
        serializer = MeSerializer(
            request.user, data=request.data, partial=True, context={"request": request}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

class FeaturesView(views.APIView):
    """GET /auth/features/ — feature flags and limits for the authenticated user."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        config = get_feature_config(request.user)
        return Response(config)


# ---------------------------------------------------------------------------
# Switch store
# ---------------------------------------------------------------------------

class SwitchStoreView(views.APIView):
    """
    POST /auth/switch-store/
    Re-issue JWT tokens with a different `active_store_public_id` claim.
    Requires `store_public_id` (public_id of the target store) in the request body.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        store_public_id = request.data.get("store_public_id")
        if not store_public_id:
            return Response(
                {"detail": "store_public_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            membership = StoreMembership.objects.select_related("store").get(
                user=request.user,
                store__public_id=store_public_id,
                is_active=True,
            )
        except (StoreMembership.DoesNotExist, ValueError):
            return Response(
                {"detail": "You do not have access to this store."},
                status=status.HTTP_403_FORBIDDEN,
            )

        store_public_id = membership.store.public_id
        profile = get_or_create_profile(request.user)
        if profile.is_enabled:
            challenge = create_challenge(
                request.user,
                flow="switch_store",
                payload={"store_public_id": store_public_id},
            )
            return Response(
                {
                    "2fa_required": True,
                    "challenge_public_id": challenge.challenge_id,
                    "flow": challenge.flow,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        return Response(_issue_tokens(request.user, store_public_id=store_public_id), status=status.HTTP_200_OK)


class TwoFactorStatusView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profile = get_or_create_profile(request.user)
        return Response(
            {
                "is_enabled": profile.is_enabled,
                "is_locked": profile.is_locked(),
                "locked_until": profile.locked_until,
            }
        )


class TwoFactorSetupView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [OTPManageRateThrottle]

    def get(self, request):
        profile = get_or_create_profile(request.user)
        if profile.is_enabled:
            return Response({"detail": "2FA is already enabled."}, status=status.HTTP_400_BAD_REQUEST)
        payload = begin_setup(request.user)
        return Response(
            {
                "is_enabled": False,
                "secret": payload["secret"],
                "provisioning_uri": payload["provisioning_uri"],
                "qr_code": payload["qr_code"],
            }
        )


class TwoFactorVerifyEnableView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [OTPManageRateThrottle]

    def post(self, request):
        serializer = OTPCodeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        ok, err = verify_setup_code(request.user, serializer.validated_data["code"])
        if not ok:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"is_enabled": True}, status=status.HTTP_200_OK)


class TwoFactorDisableView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [OTPManageRateThrottle]

    def post(self, request):
        serializer = TwoFactorDisableSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        queue_two_fa_disabled_email(request.user)
        return Response({"is_enabled": False}, status=status.HTTP_200_OK)


class TwoFactorRecoveryRequestView(views.APIView):
    """POST /auth/2fa/recovery/request/ — email a one-time recovery code (2FA must be enabled)."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [OTPManageRateThrottle]

    def post(self, request):
        ok, err = request_recovery_code(request.user)
        if not ok:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"detail": "Recovery code sent to your email."},
            status=status.HTTP_200_OK,
        )


class TwoFactorRecoveryVerifyView(views.APIView):
    """POST /auth/2fa/recovery/verify/ — verify recovery code and disable 2FA."""

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [OTPManageRateThrottle]

    def post(self, request):
        serializer = TwoFactorRecoveryVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        ok, err = verify_recovery_and_disable_2fa(request.user, serializer.validated_data["code"])
        if not ok:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)
        queue_two_fa_disabled_email(request.user)
        return Response(
            {"is_enabled": False, "detail": "2FA has been disabled successfully."},
            status=status.HTTP_200_OK,
        )


class TwoFactorChallengeVerifyView(views.APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPChallengeRateThrottle]

    def post(self, request):
        serializer = TwoFactorChallengeVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        challenge, err = verify_challenge(
            serializer.validated_data["challenge_public_id"],
            serializer.validated_data["code"],
        )
        if challenge is None:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        user = challenge.user
        store_public_id = None
        if challenge.flow == "switch_store":
            store_public_id = challenge.payload.get("store_public_id")

        return Response(_issue_tokens(user, store_public_id=store_public_id), status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Password change (authenticated)
# ---------------------------------------------------------------------------

class PasswordChangeView(views.APIView):
    """
    POST /auth/password/change/
    Allows an authenticated user to change their password by providing the current one.
    Invalidates all existing sessions (password change updates last_login hash).
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(
            {"detail": "Password changed successfully."},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Password reset (unauthenticated — two steps)
# ---------------------------------------------------------------------------

class PasswordResetRequestView(views.APIView):
    """
    POST /auth/password/reset/
    Accepts { "email": "..." } and sends a password-reset link for tenant dashboard users only
    (active StoreMembership, not staff/superuser). Others are ignored silently.
    Always returns 200 with the same message (prevents enumeration).
    """

    permission_classes = [permissions.AllowAny]
    throttle_classes = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(
            {"message": "If an account exists, we've sent a password reset link."},
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(views.APIView):
    """
    POST /auth/password/reset/confirm/
    Accepts { uid, token, new_password, new_password_confirm }.
    Validates the one-time token and sets the new password.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(
            {"detail": "Password has been reset successfully."},
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

class EmailVerifyView(views.APIView):
    """
    POST /auth/email/verify/
    Accepts { uid, token } from the verification link.
    Marks the user account as verified.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = EmailVerificationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(
            {"detail": "Email verified successfully."},
            status=status.HTTP_200_OK,
        )


class ResendVerificationView(views.APIView):
    """
    POST /auth/email/resend-verification/
    Re-sends the verification email to the authenticated user.
    Returns 400 if the account is already verified.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        if user.is_verified:
            return Response(
                {"detail": "Email is already verified."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        _send_verification_email(user, request)
        return Response(
            {"detail": "Verification email sent."},
            status=status.HTTP_200_OK,
        )
