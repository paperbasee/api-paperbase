from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    StoreAwareTokenObtainPairView,
    RegisterView,
    MeView,
    FeaturesView,
    SwitchStoreView,
    PasswordChangeView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    EmailVerifyView,
    ResendVerificationView,
)

urlpatterns = [
    # Authentication
    path("token/", StoreAwareTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("register/", RegisterView.as_view(), name="register"),

    # Profile
    path("me/", MeView.as_view(), name="auth_me"),
    path("features/", FeaturesView.as_view(), name="auth_features"),
    path("switch-store/", SwitchStoreView.as_view(), name="auth_switch_store"),

    # Password management
    path("password/change/", PasswordChangeView.as_view(), name="password_change"),
    path("password/reset/", PasswordResetRequestView.as_view(), name="password_reset"),
    path("password/reset/confirm/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),

    # Email verification
    path("email/verify/", EmailVerifyView.as_view(), name="email_verify"),
    path("email/resend-verification/", ResendVerificationView.as_view(), name="email_resend_verification"),
]
