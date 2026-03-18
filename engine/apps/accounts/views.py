from django.contrib.auth import get_user_model
from rest_framework import permissions, views, status
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from engine.apps.billing.feature_gate import get_feature_config
from engine.apps.stores.models import StoreMembership

from .serializers import MeSerializer, RegisterSerializer

User = get_user_model()


class StoreAwareTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Extend JWT payload with `active_store_id` claim.
    Also add `active_store_id` to response body for frontend routing.
    """

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        membership = (
            StoreMembership.objects.filter(user=user, is_active=True)
            .order_by("created_at")
            .first()
        )
        if membership:
            token["active_store_id"] = membership.store_id
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        membership = (
            StoreMembership.objects.filter(user=self.user, is_active=True)
            .order_by("created_at")
            .first()
        )
        data["active_store_id"] = membership.store_id if membership else None
        return data


class StoreAwareTokenObtainPairView(TokenObtainPairView):
    serializer_class = StoreAwareTokenObtainPairSerializer


class RegisterView(views.APIView):
    """Allow new users to create an account. Returns tokens for auto-login."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        membership = (
            StoreMembership.objects.filter(user=user, is_active=True)
            .order_by("created_at")
            .first()
        )
        if membership:
            refresh["active_store_id"] = membership.store_id
        access = refresh.access_token
        if membership:
            access["active_store_id"] = membership.store_id
        return Response(
            {
                "access": str(access),
                "refresh": str(refresh),
                "active_store_id": membership.store_id if membership else None,
            },
            status=status.HTTP_201_CREATED,
        )


class MeView(views.APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = MeSerializer(request.user, context={"request": request})
        return Response(serializer.data)


class FeaturesView(views.APIView):
    """GET /api/v1/auth/features/ - feature flags and limits for the authenticated user."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        config = get_feature_config(request.user)
        return Response(config)


class SwitchStoreView(views.APIView):
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
                store_id=store_id,
                is_active=True,
            )
        except (StoreMembership.DoesNotExist, ValueError):
            return Response(
                {"detail": "You do not have access to this store."},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = RefreshToken.for_user(request.user)
        refresh["active_store_id"] = membership.store_id
        access = refresh.access_token
        access["active_store_id"] = membership.store_id

        return Response(
            {"access": str(access), "refresh": str(refresh), "active_store_id": membership.store_id},
            status=status.HTTP_200_OK,
        )

