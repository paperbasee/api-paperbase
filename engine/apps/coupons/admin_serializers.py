from rest_framework import serializers

from .models import Coupon


class AdminCouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = [
            "public_id",
            "code",
            "discount_type",
            "discount_value",
            "min_order_value",
            "max_uses",
            "times_used",
            "valid_from",
            "valid_until",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "times_used", "created_at", "updated_at"]
