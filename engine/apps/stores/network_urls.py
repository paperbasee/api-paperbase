from rest_framework.routers import DefaultRouter

from .views import StoreAPIKeyManagementViewSet

router = DefaultRouter()
router.register(r"api-keys", StoreAPIKeyManagementViewSet, basename="settings-network-api-keys")

urlpatterns = router.urls
