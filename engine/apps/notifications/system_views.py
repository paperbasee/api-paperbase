from rest_framework.response import Response
from rest_framework.views import APIView

from config.permissions import IsDashboardUser

from .models import SystemNotification
from .serializers import ActiveSystemNotificationSerializer


class ActiveSystemNotificationView(APIView):
    """
    Return the single highest-priority active global dashboard notification, or null.
    Authenticated dashboard users only (no public access).
    """

    permission_classes = [IsDashboardUser]

    def get(self, request, *args, **kwargs):
        qs = SystemNotification.active_queryset().order_by("-priority", "-created_at")
        obj = qs.first()
        if obj is None:
            return Response(None)
        return Response(ActiveSystemNotificationSerializer(obj).data)
