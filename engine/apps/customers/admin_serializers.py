from rest_framework import serializers
from engine.core.serializers import SafeModelSerializer

from .models import Customer


class AdminCustomerSerializer(SafeModelSerializer):
    class Meta:
        model = Customer
        fields = [
            "public_id",
            "name",
            "phone",
            "email",
            "address",
            "total_orders",
            "total_spent",
            "first_order_at",
            "last_order_at",
            "is_repeat_customer",
            "avg_order_interval_days",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "created_at", "updated_at"]


class AdminCustomerListSerializer(SafeModelSerializer):
    class Meta:
        model = Customer
        fields = [
            "public_id",
            "name",
            "phone",
            "email",
            "address",
            "total_orders",
            "total_spent",
            "first_order_at",
            "last_order_at",
            "is_repeat_customer",
            "avg_order_interval_days",
            "created_at",
        ]
