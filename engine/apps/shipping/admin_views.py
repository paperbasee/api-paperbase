from django.db import IntegrityError
from rest_framework import mixins, viewsets
from rest_framework.exceptions import ValidationError

from config.permissions import IsDashboardUser
from engine.core.tenancy import get_active_store

from .models import ShippingZone, ShippingMethod, ShippingRate
from .admin_serializers import (
    AdminShippingZoneSerializer,
    AdminShippingMethodSerializer,
    AdminShippingRateSerializer,
)


class _AdminStoreScopedViewSet(viewsets.GenericViewSet):
    permission_classes = [IsDashboardUser]

    def _get_store_or_error(self):
        ctx = get_active_store(self.request)
        if not ctx.store:
            raise ValidationError(
                {
                    "detail": (
                        "No active store resolved. Re-login, switch store, or send the "
                        "X-Store-ID header."
                    )
                }
            )
        return ctx.store


class AdminShippingZoneViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    _AdminStoreScopedViewSet,
):
    serializer_class = AdminShippingZoneSerializer
    queryset = ShippingZone.objects.all()

    def get_queryset(self):
        store = self._get_store_or_error()
        return super().get_queryset().filter(store=store).order_by("name")

    def perform_create(self, serializer):
        store = self._get_store_or_error()
        name = (serializer.validated_data.get("name") or "").strip()
        if name and ShippingZone.objects.filter(store=store, name__iexact=name).exists():
            raise ValidationError({"name": ["A zone with this name already exists in your store."]})
        try:
            serializer.save(store=store)
        except IntegrityError:
            raise ValidationError({"detail": "Could not save shipping zone due to a conflicting rule."})

    def perform_update(self, serializer):
        store = self._get_store_or_error()
        name = (serializer.validated_data.get("name") or "").strip()
        if name:
            qs = ShippingZone.objects.filter(store=store, name__iexact=name)
            if serializer.instance is not None:
                qs = qs.exclude(pk=serializer.instance.pk)
            if qs.exists():
                raise ValidationError({"name": ["A zone with this name already exists in your store."]})
        try:
            serializer.save()
        except IntegrityError:
            raise ValidationError({"detail": "Could not update shipping zone due to a conflicting rule."})


class AdminShippingMethodViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    _AdminStoreScopedViewSet,
):
    serializer_class = AdminShippingMethodSerializer
    queryset = ShippingMethod.objects.prefetch_related("zones").all()

    def get_queryset(self):
        store = self._get_store_or_error()
        return super().get_queryset().filter(store=store).order_by("order", "name")

    def get_serializer(self, *args, **kwargs):
        ser = super().get_serializer(*args, **kwargs)
        store = get_active_store(self.request).store
        if store and hasattr(ser, "fields") and "zone_ids" in ser.fields:
            ser.fields["zone_ids"].queryset = ShippingZone.objects.filter(store=store)
        return ser

    def perform_create(self, serializer):
        store = self._get_store_or_error()
        name = (serializer.validated_data.get("name") or "").strip()
        if name and ShippingMethod.objects.filter(store=store, name__iexact=name).exists():
            raise ValidationError({"name": ["A method with this name already exists in your store."]})
        try:
            serializer.save(store=store)
        except IntegrityError:
            raise ValidationError({"detail": "Could not save shipping method due to a conflicting rule."})

    def perform_update(self, serializer):
        store = self._get_store_or_error()
        name = (serializer.validated_data.get("name") or "").strip()
        if name:
            qs = ShippingMethod.objects.filter(store=store, name__iexact=name)
            if serializer.instance is not None:
                qs = qs.exclude(pk=serializer.instance.pk)
            if qs.exists():
                raise ValidationError({"name": ["A method with this name already exists in your store."]})
        try:
            serializer.save()
        except IntegrityError:
            raise ValidationError({"detail": "Could not update shipping method due to a conflicting rule."})


class AdminShippingRateViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    _AdminStoreScopedViewSet,
):
    serializer_class = AdminShippingRateSerializer
    queryset = ShippingRate.objects.select_related("shipping_method", "shipping_zone").all()

    def get_queryset(self):
        store = self._get_store_or_error()
        return super().get_queryset().filter(store=store).order_by("shipping_method_id", "shipping_zone_id", "id")

    def get_serializer(self, *args, **kwargs):
        ser = super().get_serializer(*args, **kwargs)
        store = get_active_store(self.request).store
        if store and hasattr(ser, "fields"):
            if "shipping_method" in ser.fields:
                ser.fields["shipping_method"].queryset = ShippingMethod.objects.filter(store=store)
            if "shipping_zone" in ser.fields:
                ser.fields["shipping_zone"].queryset = ShippingZone.objects.filter(store=store)
        return ser

    def perform_create(self, serializer):
        store = self._get_store_or_error()
        try:
            serializer.save(store=store)
        except IntegrityError:
            raise ValidationError(
                {
                    "detail": (
                        "A shipping rate with the same method/zone and the same order-total range already exists."
                    )
                }
            )

    def perform_update(self, serializer):
        try:
            serializer.save()
        except IntegrityError:
            raise ValidationError(
                {
                    "detail": (
                        "A shipping rate with the same method/zone and the same order-total range already exists."
                    )
                }
            )

