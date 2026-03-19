from django.contrib.auth import get_user_model
from rest_framework import permissions, views, status
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from engine.apps.billing.feature_gate import get_feature_config
from engine.apps.stores.models import StoreMembership

from .serializers import (
    MeSerializer,
    RegisterSerializer,
    PasswordChangeSerializer,
    PasswordResetSerializer,
    PasswordResetConfirmSerializer,
    EmailVerificationSerializer,
    _send_verification_email,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# JWT — store-aware token
# ---------------------------------------------------------------------------

class StoreAwareTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Extend JWT payload with `active_store_id` claim (store.public_id).
    Also add `active_store_id` to response body for frontend routing.
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
            token["active_store_id"] = membership.store.public_id
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        membership = (
            StoreMembership.objects.select_related("store")
            .filter(user=self.user, is_active=True)
            .order_by("created_at")
            .first()
        )
        data["active_store_id"] = membership.store.public_id if membership else None
        return data


class StoreAwareTokenObtainPairView(TokenObtainPairView):
    serializer_class = StoreAwareTokenObtainPairSerializer


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

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        membership = (
            StoreMembership.objects.select_related("store")
            .filter(user=user, is_active=True)
            .order_by("created_at")
            .first()
        )
        store_public_id = membership.store.public_id if membership else None
        if membership:
            refresh["active_store_id"] = store_public_id
        access = refresh.access_token
        if membership:
            access["active_store_id"] = store_public_id
        return Response(
            {
                "access": str(access),
                "refresh": str(refresh),
                "active_store_id": store_public_id,
            },
            status=status.HTTP_201_CREATED,
        )


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
    Re-issue JWT tokens with a different `active_store_id` claim.
    Requires `store_id` (public_id of the target store) in the request body.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        store_id = request.data.get("store_id")
        if not store_id:
            return Response(
                {"detail": "store_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            membership = StoreMembership.objects.select_related("store").get(
                user=request.user,
                store__public_id=store_id,
                is_active=True,
            )
        except (StoreMembership.DoesNotExist, ValueError):
            return Response(
                {"detail": "You do not have access to this store."},
                status=status.HTTP_403_FORBIDDEN,
            )

        store_public_id = membership.store.public_id
        refresh = RefreshToken.for_user(request.user)
        refresh["active_store_id"] = store_public_id
        access = refresh.access_token
        access["active_store_id"] = store_public_id

        return Response(
            {
                "access": str(access),
                "refresh": str(refresh),
                "active_store_id": store_public_id,
            },
            status=status.HTTP_200_OK,
        )


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
    Accepts { "email": "..." } and sends a password-reset link.
    Always returns 200 regardless of whether the email exists (prevents enumeration).
    """

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(
            {"detail": "If that email is registered, a reset link has been sent."},
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
