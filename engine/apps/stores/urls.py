from rest_framework.routers import DefaultRouter

from .views import StoreMembershipViewSet, StoreSettingsViewSet, StoreViewSet

router = DefaultRouter()
router.register(r"", StoreViewSet, basename="stores")
router.register(r"memberships", StoreMembershipViewSet, basename="store-memberships")
router.register(r"settings", StoreSettingsViewSet, basename="store-settings")

urlpatterns = router.urls

