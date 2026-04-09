from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response

from engine.utils.time import bd_calendar_date
from rest_framework.views import APIView

from config.permissions import DenyAPIKeyAccess, IsDashboardUser
from engine.core.tenant_drf import ProvenTenantContextMixin

from .models import NotificationDismissal, PlatformNotification
from .serializers import ActiveSystemNotificationSerializer


class ActiveSystemNotificationView(ProvenTenantContextMixin, APIView):
    """
    Return the single highest-priority active global dashboard notification, or null.
    Respects per-user daily dismiss counts. Authenticated dashboard users only.
    """

    permission_classes = [DenyAPIKeyAccess, IsDashboardUser]

    def get(self, request, *args, **kwargs):
        qs = PlatformNotification.visible_for_user_queryset(request.user)
        obj = qs.first()
        if obj is None:
            return Response(None)
        return Response(ActiveSystemNotificationSerializer(obj).data)


class DismissSystemNotificationView(ProvenTenantContextMixin, APIView):
    """Record a dismiss for today; hides for the rest of the day after daily_limit."""

    permission_classes = [DenyAPIKeyAccess, IsDashboardUser]

    def post(self, request, public_id, *args, **kwargs):
        notification = get_object_or_404(PlatformNotification, public_id=public_id)
        now = timezone.now()
        today = bd_calendar_date(now)
        with transaction.atomic():
            obj, _ = NotificationDismissal.objects.get_or_create(
                user=request.user,
                notification=notification,
                date=today,
                defaults={"dismiss_count": 0},
            )
            NotificationDismissal.objects.filter(pk=obj.pk).update(
                dismiss_count=F("dismiss_count") + 1
            )
            obj.refresh_from_db()
        hidden = obj.dismiss_count >= notification.daily_limit
        return Response(
            {
                "public_id": notification.public_id,
                "dismiss_count": obj.dismiss_count,
                "hidden": hidden,
            }
        )
