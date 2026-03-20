from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from engine.core.activity import log_activity
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store

from .models import MarketingIntegration
from .serializers import (
    IntegrationEventSettingsSerializer,
    MarketingIntegrationConnectSerializer,
    MarketingIntegrationSerializer,
    MarketingIntegrationUpdateSerializer,
)


class AdminMarketingIntegrationViewSet(
    StoreRolePermissionMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = MarketingIntegration.objects.select_related("event_settings").all()
    lookup_field = "public_id"

    def get_serializer_class(self):
        if self.action == "create":
            return MarketingIntegrationConnectSerializer
        if self.action in ("update", "partial_update"):
            return MarketingIntegrationUpdateSerializer
        if self.action == "events":
            return IntegrationEventSettingsSerializer
        return MarketingIntegrationSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        return qs.filter(store=ctx.store)

    def create(self, request, *args, **kwargs):
        ctx = get_active_store(request)
        if not ctx.store:
            raise ValidationError(
                {"detail": "No active store resolved. Re-login, switch store, or send the X-Store-ID header."}
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(store=ctx.store)
        log_activity(
            request=request,
            action=ActivityLog.Action.CREATE,
            entity_type="marketing_integration",
            entity_id=instance.public_id,
            summary=f"Marketing integration connected: {instance.get_provider_display()}",
        )
        return Response(
            MarketingIntegrationSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = MarketingIntegrationUpdateSerializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_activity(
            request=request,
            action=ActivityLog.Action.UPDATE,
            entity_type="marketing_integration",
            entity_id=instance.public_id,
            summary=f"Marketing integration updated: {instance.get_provider_display()}",
        )
        return Response(MarketingIntegrationSerializer(instance).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        public_id = instance.public_id
        provider = instance.get_provider_display()
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="marketing_integration",
            entity_id=public_id,
            summary=f"Marketing integration disconnected: {provider}",
        )

    @action(detail=True, methods=["get", "patch"], url_path="events")
    def events(self, request, public_id=None):
        integration = self.get_object()
        settings, _ = integration.event_settings.__class__.objects.get_or_create(
            integration=integration,
        )

        if request.method == "PATCH":
            serializer = IntegrationEventSettingsSerializer(settings, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            log_activity(
                request=request,
                action=ActivityLog.Action.UPDATE,
                entity_type="marketing_integration",
                entity_id=integration.public_id,
                summary=f"Event settings updated for {integration.get_provider_display()}",
            )
            return Response(serializer.data)

        return Response(IntegrationEventSettingsSerializer(settings).data)
