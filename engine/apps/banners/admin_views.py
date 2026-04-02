from rest_framework import viewsets
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.exceptions import ValidationError

from engine.core.activity import log_activity
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.media_deletion_service import schedule_media_deletion
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store

from .models import Banner
from .admin_serializers import AdminBannerSerializer
from .services import invalidate_banner_cache


class AdminBannerViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = AdminBannerSerializer
    queryset = Banner.objects.all()
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        return qs.filter(store=ctx.store)

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValidationError(
                {
                    "detail": (
                        "No active store resolved. Re-login, switch store, or send the "
                        "X-Store-ID header."
                    )
                }
            )
        instance = serializer.save(store=store)
        invalidate_banner_cache(store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="banner",
            entity_id=instance.public_id,
            summary=f"Banner created: {instance.title or instance.public_id}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        ctx = get_active_store(self.request)
        if ctx.store:
            invalidate_banner_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="banner",
            entity_id=instance.public_id,
            summary=f"Banner updated: {instance.title or instance.public_id}",
        )

    def perform_destroy(self, instance):
        title = instance.title or instance.public_id
        public_id = instance.public_id
        schedule_media_deletion(instance)
        ctx = get_active_store(self.request)
        super().perform_destroy(instance)
        if ctx.store:
            invalidate_banner_cache(ctx.store.public_id)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="banner",
            entity_id=public_id,
            summary=f"Banner deleted: {title}",
        )
