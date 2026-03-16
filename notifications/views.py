from django.db import models
from rest_framework.generics import ListAPIView

from .models import Notification
from .serializers import NotificationSerializer


class ActiveNotificationListView(ListAPIView):
    """List all currently active notifications (for banner display)."""
    serializer_class = NotificationSerializer
    permission_classes = []  # Public endpoint
    authentication_classes = []

    def get_queryset(self):
        """Return only notifications that are currently active."""
        qs = Notification.objects.filter(is_active=True)
        # Filter by date range if set
        from django.utils import timezone
        now = timezone.now()
        qs = qs.filter(
            models.Q(start_date__isnull=True) | models.Q(start_date__lte=now),
            models.Q(end_date__isnull=True) | models.Q(end_date__gte=now)
        )
        return qs
