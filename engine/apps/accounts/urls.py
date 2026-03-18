from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    StoreAwareTokenObtainPairView,
    RegisterView,
    MeView,
    FeaturesView,
    SwitchStoreView,
)

urlpatterns = [
    path("token/", StoreAwareTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("register/", RegisterView.as_view(), name="register"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/", MeView.as_view(), name="auth_me"),
    path("features/", FeaturesView.as_view(), name="auth_features"),
    path("switch-store/", SwitchStoreView.as_view(), name="auth_switch_store"),
]
