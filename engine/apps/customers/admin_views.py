from decimal import Decimal
from datetime import timedelta

from django.db.models import Count, Min, Max, Sum
from django.db.models import Q
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from config.permissions import IsDashboardUser
from engine.core.activity import log_activity
from engine.core.admin_views import StoreRolePermissionMixin
from engine.core.models import ActivityLog
from engine.core.tenancy import get_active_store
from engine.apps.orders.models import Order

from .models import Customer, CustomerAddress
from .admin_serializers import (
    AdminCustomerSerializer,
    AdminCustomerListSerializer,
    AdminCustomerAddressSerializer,
)


class AdminCustomerViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    queryset = Customer.objects.select_related("user").prefetch_related("addresses").all()
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
            qs = qs.filter(created_at__date=timezone.localdate())
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
                | Q(user__email__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )

        return qs

    def perform_create(self, serializer):
        ctx = get_active_store(self.request)
        store = ctx.store
        if not store:
            raise ValueError("No active store for customer creation")
        instance = serializer.save(store=store)
        label = (instance.email or (instance.user.email if instance.user else "") or instance.phone)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.CREATE,
            entity_type="customer",
            entity_id=instance.public_id,
            summary=f"Customer created: {label}",
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        label = (instance.email or (instance.user.email if instance.user else "") or instance.phone)
        log_activity(
            request=self.request,
            action=ActivityLog.Action.UPDATE,
            entity_type="customer",
            entity_id=instance.public_id,
            summary=f"Customer updated: {label}",
        )

    def perform_destroy(self, instance):
        email = instance.email or (instance.user.email if instance.user else "") or instance.phone
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
        orders_qs = Order.objects.filter(store=customer.store, customer=customer)
        analytics = orders_qs.aggregate(
            total_orders=Count("id"),
            total_spent=Sum("total"),
            first_order_date=Min("created_at"),
            last_order_date=Max("created_at"),
        )
        total_orders = analytics["total_orders"] or 0
        total_spent = analytics["total_spent"] or Decimal("0.00")
        average_order_value = (
            total_spent / total_orders if total_orders else Decimal("0.00")
        )
        loyalty_score = (Decimal(total_orders) * Decimal("2")) + (
            total_spent / Decimal("100")
        )
        latest_district = (
            orders_qs.exclude(district="")
            .order_by("-created_at")
            .values_list("district", flat=True)
            .first()
        )
        ordered_products = []
        ordered_orders = orders_qs.prefetch_related("items__product").order_by("-created_at")
        for order in ordered_orders:
            for item in order.items.all():
                ordered_products.append(
                    {
                        "order_public_id": order.public_id,
                        "order_number": order.order_number,
                        "ordered_at": order.created_at,
                        "product_public_id": item.product.public_id,
                        "product_name": item.product.name,
                        "quantity": item.quantity,
                        "price": item.price,
                    }
                )

        payload = {
            "customer": {
                "public_id": customer.public_id,
                "name": customer.name,
                "email": customer.email,
                "phone": customer.phone,
                "address": customer.address,
                "district": latest_district,
            },
            "analytics": {
                "total_orders": total_orders,
                "total_spent": total_spent,
                "average_order_value": average_order_value,
                "first_order_date": analytics["first_order_date"],
                "last_order_date": analytics["last_order_date"],
                "loyalty_score": loyalty_score,
            },
            "ordered_products": ordered_products,
        }
        return Response(payload)


class AdminCustomerAddressViewSet(StoreRolePermissionMixin, viewsets.ModelViewSet):
    serializer_class = AdminCustomerAddressSerializer
    queryset = CustomerAddress.objects.select_related("customer").all()
    lookup_field = 'public_id'

    def get_queryset(self):
        qs = super().get_queryset()
        ctx = get_active_store(self.request)
        if not ctx.store:
            return qs.none()
        qs = qs.filter(customer__store=ctx.store)
        customer_public_id = self.request.query_params.get("customer")
        if customer_public_id:
            qs = qs.filter(customer__public_id=customer_public_id)
        return qs
