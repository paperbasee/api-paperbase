from rest_framework import viewsets, mixins

from config.permissions import IsDashboardUser
from engine.core.tenancy import get_active_store
from .models import WishlistItem
from .admin_serializers import AdminWishlistItemSerializer


class AdminWishlistItemViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsDashboardUser]
    serializer_class = AdminWishlistItemSerializer
    queryset = WishlistItem.objects.select_related('user', 'product').all()
    lookup_field = "public_id"
    lookup_url_kwarg = "public_id"

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        return qs.filter(product__store=ctx.store)
