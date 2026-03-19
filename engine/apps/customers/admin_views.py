from rest_framework import viewsets

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store

from .models import Customer, CustomerAddress
from .admin_serializers import (
    AdminCustomerSerializer,
    AdminCustomerListSerializer,
    AdminCustomerAddressSerializer,
)


class AdminCustomerViewSet(viewsets.ModelViewSet):
    permission_classes = [IsDashboardUser]
    queryset = Customer.objects.select_related("user").prefetch_related("addresses").all()
    lookup_field = 'public_id'

    def get_serializer_class(self):
        if self.action == "list":
            return AdminCustomerListSerializer
        return AdminCustomerSerializer

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
            raise ValueError("No active store for customer creation")
        instance = serializer.save(store=store)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="customer",
            entity_id=instance.pk,
            summary=f"Customer created: {instance.user.email}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="customer",
            entity_id=instance.pk,
            summary=f"Customer updated: {instance.user.email}",
        )

    def perform_destroy(self, instance):
        email = instance.user.email
        pk = instance.pk
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="customer",
            entity_id=pk,
            summary=f"Customer deleted: {email}",
        )


class AdminCustomerAddressViewSet(viewsets.ModelViewSet):
    permission_classes = [IsDashboardUser]
    serializer_class = AdminCustomerAddressSerializer
    queryset = CustomerAddress.objects.select_related("customer").all()
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        customer_id = self.request.query_params.get("customer")
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
        return qs
