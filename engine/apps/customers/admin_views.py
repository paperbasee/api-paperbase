from decimal import Decimal
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import viewsets

from engine.utils.bd_query import filter_by_bd_date
from engine.utils.time import bd_today
from rest_framework.decorators import action
from rest_framework.response import Response

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.models import ActivityLog
from engine.core.tenancy import assert_instance_belongs_to_store, get_active_store
from .models import Customer
from .admin_serializers import (
    AdminCustomerSerializer,
    AdminCustomerListSerializer,
)


class AdminCustomerViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    lookup_field = 'public_id'

    def get_serializer_class(self):
        if self.action == "list":
            return AdminCustomerListSerializer
        return AdminCustomerSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        qs = qs.filter(store=ctx.store).order_by("-created_at", "id")

        joined_date = (self.request.query_params.get("joined_date") or "").strip().lower()
        if joined_date == "today":
            qs = filter_by_bd_date(qs, "created_at", bd_today())
        elif joined_date == "last_7_days":
            qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=7))
        elif joined_date == "last_30_days":
            qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=30))

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
            )

        return qs

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValueError("No active store for customer creation")
        instance = serializer.save(store=store)
        label = (instance.email or instance.phone)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="customer",
            entity_id=instance.public_id,
            summary=f"Customer created: {label}",
        )

    def perform_update(self, serializer):
        ctx = get_active_store(self.request)
        assert_instance_belongs_to_store(serializer.instance, ctx.store)
        instance = serializer.save()
        label = (instance.email or instance.phone)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="customer",
            entity_id=instance.public_id,
            summary=f"Customer updated: {label}",
        )

    def perform_destroy(self, instance):
        ctx = get_active_store(self.request)
        assert_instance_belongs_to_store(instance, ctx.store)
        email = instance.email or instance.phone
        public_id = instance.public_id
        super().perform_destroy(instance)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.DELETE,
            entity_type="customer",
            entity_id=public_id,
            summary=f"Customer deleted: {email}",
        )

    @action(detail=True, methods=["get"], url_path="details")
    def details(self, request, public_id=None):
        customer = self.get_object()
        payload = {
            "customer": {
                "public_id": customer.public_id,
                "name": customer.name,
                "email": customer.email,
                "phone": customer.phone,
                "address": customer.address,
            },
            "analytics": {
                "total_orders": int(customer.total_orders or 0),
                "total_spent": customer.total_spent,
                "first_order_at": customer.first_order_at,
                "last_order_at": customer.last_order_at,
                "is_repeat_customer": bool(customer.is_repeat_customer),
                "avg_order_interval_days": customer.avg_order_interval_days,
            },
        }
        return Response(payload)
