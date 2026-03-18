from rest_framework import viewsets, mixins

from config.permissions import IsDashboardUser
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
