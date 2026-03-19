from rest_framework import viewsets
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store

from .models import Banner
from .admin_serializers import AdminBannerSerializer


class AdminBannerViewSet(viewsets.ModelViewSet):
    permission_classes = [IsDashboardUser]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminBannerSerializer
    queryset = Banner.objects.all()
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if ctx.store:
            return qs.filter(store=ctx.store)
        return qs

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValueError("No active store for banner creation")
        instance = serializer.save(store=store)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="banner",
            entity_id=instance.pk,
            summary=f"Banner created: {instance.title or instance.position}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="banner",
            entity_id=instance.pk,
            summary=f"Banner updated: {instance.title or instance.position}",
        )

    def perform_destroy(self, instance):
        title = instance.title or instance.position
        pk = instance.pk
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="banner",
            entity_id=pk,
            summary=f"Banner deleted: {title}",
        )
