from rest_framework import serializers

from .models import ShippingZone, ShippingMethod, ShippingRate


class AdminShippingZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingZone
        fields = [
            "id",
            "name",
            "delivery_areas",
            "districts",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AdminShippingMethodSerializer(serializers.ModelSerializer):
    zone_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        required=False,
        queryset=ShippingZone.objects.all(),
        source="zones",
    )

    class Meta:
        model = ShippingMethod
        fields = [
            "id",
            "name",
            "method_type",
            "is_active",
            "order",
            "zone_ids",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AdminShippingRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingRate
        fields = [
            "id",
            "shipping_method",
            "shipping_zone",
            "rate_type",
            "min_order_total",
            "max_order_total",
            "price",
            "is_active",
        ]
        read_only_fields = ["id"]

