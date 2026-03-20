from rest_framework import viewsets, mixins, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from engine.core.activity import log_activity
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store

from .models import Courier
from .serializers import (
    CourierConnectSerializer,
    CourierSerializer,
    CourierUpdateSerializer,
)


class AdminCourierViewSet(
    StoreRolePermissionMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Courier.objects.all()
    lookup_field = "public_id"

    def get_serializer_class(self):
        if self.action == "create":
            return CourierConnectSerializer
        if self.action in ("update", "partial_update"):
            return CourierUpdateSerializer
        return CourierSerializer

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
            entity_type="courier",
            entity_id=instance.public_id,
            summary=f"Courier connected: {instance.get_provider_display()}",
        )
        return Response(
            CourierSerializer(instance).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = CourierUpdateSerializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_activity(
            request=request,
            action=ActivityLog.Action.UPDATE,
            entity_type="courier",
            entity_id=instance.public_id,
            summary=f"Courier updated: {instance.get_provider_display()}",
        )
        return Response(CourierSerializer(instance).data)

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
            entity_type="courier",
            entity_id=public_id,
            summary=f"Courier disconnected: {provider}",
        )
