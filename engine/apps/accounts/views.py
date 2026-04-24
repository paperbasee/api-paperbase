from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, views, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from engine.apps.billing.feature_gate import get_feature_config
from engine.apps.emails.triggers import queue_two_fa_disabled_email
from engine.apps.stores.models import Store, StoreMembership
from engine.core.rate_limit_service import RateLimitExceeded
from config.permissions import IsVerifiedUser
from .models import UserTwoFactor
from .two_factor_service import (
    begin_setup,
    resolve_two_factor_issuer,
    create_challenge,
    get_or_create_profile,
    request_recovery_code,
    get_active_challenge,
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
    ResendVerificationSerializer,
    OTPCodeSerializer,
    TwoFactorChallengeVerifySerializer,
    TwoFactorDisableSerializer,
    TwoFactorChallengeRecoveryRequestSerializer,
    TwoFactorChallengeRecoveryVerifySerializer,
    TwoFactorRecoveryVerifySerializer,
)
from .services import (
    RESEND_VERIFICATION_NEUTRAL_MESSAGE,
    resend_verification_email_for_email,
)
from .throttles import (
    LoginRateThrottle,
    OTPChallengeRateThrottle,
    OTPManageRateThrottle,
    PasswordResetRateThrottle,
    RegisterRateThrottle,
)
from .turnstile import verify_turnstile_request

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
        pid = _get_first_active_store_public_id(user)
        if pid:
            token["active_store_public_id"] = pid
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        data["active_store_public_id"] = _get_first_active_store_public_id(self.user)
        return data


def _get_first_active_store_public_id(user):
    owned = getattr(user, "owned_store", None)
    if owned and owned.status == Store.Status.ACTIVE:
        return owned.public_id
    membership = (
        StoreMembership.objects.select_related("store")
        .filter(user=user, is_active=True, store__status=Store.Status.ACTIVE)
        .order_by("created_at")
        .first()
    )
    return membership.store.public_id if membership else None


def _issue_tokens(user, store_public_id=None):
    resolved = store_public_id
    if resolved:
        ok = StoreMembership.objects.filter(
            user=user,
            store__public_id=resolved,
            is_active=True,
            store__status=Store.Status.ACTIVE,
        ).exists()
        if not ok:
            resolved = None
    resolved_store_public_id = resolved or _get_first_active_store_public_id(user)
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


def _email_not_verified_response():
    return Response(
        {
            "detail": "Email verification is required.",
            "code": "email_not_verified",
        },
        status=status.HTTP_403_FORBIDDEN,
    )


class StrictTokenRefreshSerializer(TokenRefreshSerializer):
    """
    Harden refresh flow:
    - Never crash if token points to a missing user
    - Block refresh for unverified users
    """

    def validate(self, attrs):
        try:
            data = super().validate(attrs)
        except User.DoesNotExist as exc:
            raise InvalidToken("Token is invalid or expired.") from exc

        refresh = RefreshToken(attrs["refresh"])
        user_public_id = refresh.get(api_settings.USER_ID_CLAIM)
        if not user_public_id:
            raise InvalidToken("Token is invalid or expired.")

        user = User.objects.filter(public_id=user_public_id).first()
        if user is None or not user.is_active:
            raise InvalidToken("Token is invalid or expired.")
        if not user.is_verified:
            raise PermissionDenied(
                {
                    "detail": "Email verification is required.",
                    "code": "email_not_verified",
                }
            )
        return data


class StoreAwareTokenRefreshSerializer(StrictTokenRefreshSerializer):
    """
    After validation, align `active_store_public_id` on the access token with
    the user's first ACTIVE store membership (not suspended/inactive stores).
    """

    def validate(self, attrs):
        data = super().validate(attrs)
        refresh_str = data.get("refresh") or attrs.get("refresh")
        if not refresh_str:
            return data
        refresh = RefreshToken(refresh_str)
        user_public_id = refresh.get(api_settings.USER_ID_CLAIM)
        if not user_public_id:
            return data
        user = User.objects.filter(public_id=user_public_id).first()
        if user is None:
            return data
        store_pid = _get_first_active_store_public_id(user)
        access = refresh.access_token
        if store_pid:
            access["active_store_public_id"] = store_pid
        else:
            access.pop("active_store_public_id", None)
        data["access"] = str(access)
        return data


class StrictTokenRefreshView(TokenRefreshView):
    serializer_class = StoreAwareTokenRefreshSerializer


class StoreAwareTokenObtainPairView(views.APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [] if getattr(settings, "TESTING", False) else [LoginRateThrottle]

    def post(self, request):
        ok, turnstile_err = verify_turnstile_request(request)
        if not ok:
            return Response({"detail": turnstile_err}, status=status.HTTP_400_BAD_REQUEST)

        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        if not email or not password:
            return Response({"detail": "Email and password are required."}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(request, username=email, password=password)
        if not user:
            return Response({"detail": "No active account found with the given credentials"}, status=status.HTTP_401_UNAUTHORIZED)
        if not user.is_verified:
            return _email_not_verified_response()

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
    Create a new user account.
    A verification email is sent automatically.
    """

    permission_classes = [permissions.AllowAny]
    throttle_classes = [RegisterRateThrottle]

    def post(self, request):
        ok, turnstile_err = verify_turnstile_request(request)
        if not ok:
            return Response({"detail": turnstile_err}, status=status.HTTP_400_BAD_REQUEST)

        reg_payload = request.data.copy()
        reg_payload.pop("cf_turnstile_response", None)
        reg_payload.pop("cf-turnstile-response", None)
        serializer = RegisterSerializer(data=reg_payload)
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
        return Response(
            {
                "detail": "Registration successful. Please verify your email before signing in.",
                "email_verification_required": True,
            },
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Me / Profile
# ---------------------------------------------------------------------------

class MeView(views.APIView):
    """
    GET  /auth/me/  — return authenticated user profile + store memberships
    PATCH /auth/me/ — update first_name, last_name, phone, avatar_seed
    """

    permission_classes = [permissions.IsAuthenticated, IsVerifiedUser]

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

    permission_classes = [permissions.IsAuthenticated, IsVerifiedUser]

    def get(self, request):
        config = get_feature_config(request.user)
        return Response(config)


class TwoFactorStatusView(views.APIView):
    permission_classes = [permissions.IsAuthenticated, IsVerifiedUser]

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
    permission_classes = [permissions.IsAuthenticated, IsVerifiedUser]
    throttle_classes = [OTPManageRateThrottle]

    def get(self, request):
        profile = get_or_create_profile(request.user)
        if profile.is_enabled:
            return Response({"detail": "2FA is already enabled."}, status=status.HTTP_400_BAD_REQUEST)
        payload = begin_setup(request.user, issuer_name=resolve_two_factor_issuer(request))
        return Response(
            {
                "is_enabled": False,
                "secret": payload["secret"],
                "provisioning_uri": payload["provisioning_uri"],
                "qr_code": payload["qr_code"],
            }
        )


class TwoFactorVerifyEnableView(views.APIView):
    permission_classes = [permissions.IsAuthenticated, IsVerifiedUser]
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
    permission_classes = [permissions.IsAuthenticated, IsVerifiedUser]
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

    permission_classes = [permissions.IsAuthenticated, IsVerifiedUser]
    throttle_classes = [OTPManageRateThrottle]

    def post(self, request):
        try:
            ok, err = request_recovery_code(request.user)
        except RateLimitExceeded as exc:
            return Response(exc.as_response_data(), status=status.HTTP_429_TOO_MANY_REQUESTS)
        if not ok:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"detail": "Recovery code sent to your email."},
            status=status.HTTP_200_OK,
        )


class TwoFactorRecoveryVerifyView(views.APIView):
    """POST /auth/2fa/recovery/verify/ — verify recovery code and disable 2FA."""

    permission_classes = [permissions.IsAuthenticated, IsVerifiedUser]
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


class TwoFactorChallengeRecoveryRequestView(views.APIView):
    """POST /auth/2fa/challenge/recovery/request/ — email recovery code during login challenge."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPChallengeRateThrottle]

    def post(self, request):
        serializer = TwoFactorChallengeRecoveryRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        challenge, err = get_active_challenge(
            serializer.validated_data["challenge_public_id"],
            flow="login",
        )
        if challenge is None:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        user = challenge.user
        if not user.is_verified:
            return _email_not_verified_response()

        request_email = serializer.validated_data["email"]
        if request_email != (user.email or "").strip().lower():
            return Response(
                {
                    "detail": "No account found for this email in the current recovery flow.",
                    "sent": False,
                },
                status=status.HTTP_200_OK,
            )

        try:
            ok, err = request_recovery_code(user)
        except RateLimitExceeded as exc:
            return Response(exc.as_response_data(), status=status.HTTP_429_TOO_MANY_REQUESTS)
        if not ok:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"detail": "Recovery code sent to your email.", "sent": True},
            status=status.HTTP_200_OK,
        )


class TwoFactorChallengeRecoveryVerifyView(views.APIView):
    """POST /auth/2fa/challenge/recovery/verify/ — verify recovery code and complete login."""

    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPChallengeRateThrottle]

    def post(self, request):
        serializer = TwoFactorChallengeRecoveryVerifySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            challenge, err = get_active_challenge(
                serializer.validated_data["challenge_public_id"],
                flow="login",
                for_update=True,
            )
            if challenge is None:
                return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

            user = challenge.user
            if not user.is_verified:
                return _email_not_verified_response()

            ok, err = verify_recovery_and_disable_2fa(user, serializer.validated_data["code"])
            if not ok:
                return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

            challenge.consumed_at = timezone.now()
            challenge.save(update_fields=["consumed_at", "updated_at"])
            queue_two_fa_disabled_email(user)
            return Response(_issue_tokens(user), status=status.HTTP_200_OK)


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
        if not user.is_verified:
            return _email_not_verified_response()

        return Response(_issue_tokens(user), status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Password change (authenticated)
# ---------------------------------------------------------------------------

class PasswordChangeView(views.APIView):
    """
    POST /auth/password/change/
    Allows an authenticated user to change their password by providing the current one.
    Invalidates all existing sessions (password change updates last_login hash).
    """

    permission_classes = [permissions.IsAuthenticated, IsVerifiedUser]

    def post(self, request):
        serializer = PasswordChangeSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.save()
        logout_all_devices = serializer.validated_data.get("logout_all_devices", False)
        response_payload = {"detail": "Password changed successfully."}
        if logout_all_devices:
            response_payload.update(_issue_tokens(user))
        return Response(
            response_payload,
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
        try:
            serializer.save()
        except RateLimitExceeded as exc:
            return Response(exc.as_response_data(), status=status.HTTP_429_TOO_MANY_REQUESTS)
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
    Re-sends the verification email using email input.
    Always returns a neutral success response to prevent user enumeration.
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ResendVerificationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            resend_verification_email_for_email(serializer.validated_data["email"])
        except RateLimitExceeded as exc:
            return Response(exc.as_response_data(), status=status.HTTP_429_TOO_MANY_REQUESTS)
        return Response(
            {"message": RESEND_VERIFICATION_NEUTRAL_MESSAGE},
            status=status.HTTP_200_OK,
        )
