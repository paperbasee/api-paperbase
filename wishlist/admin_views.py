from rest_framework import viewsets, mixins

from config.permissions import IsStaffUser
from .models import WishlistItem
from .admin_serializers import AdminWishlistItemSerializer


class AdminWishlistItemViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsStaffUser]
    serializer_class = AdminWishlistItemSerializer
    queryset = WishlistItem.objects.select_related('user', 'product').all()
