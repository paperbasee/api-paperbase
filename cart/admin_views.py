from rest_framework import viewsets, mixins

from config.permissions import IsStaffUser
from .models import Cart
from .admin_serializers import AdminCartSerializer


class AdminCartViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = [IsStaffUser]
    serializer_class = AdminCartSerializer
    queryset = Cart.objects.select_related('user').prefetch_related(
        'items__product',
    ).all()
