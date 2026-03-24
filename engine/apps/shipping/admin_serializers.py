from rest_framework import serializers

from .models import ShippingZone, ShippingMethod, ShippingRate


class AdminShippingZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingZone
        fields = [
            "public_id",
            "name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "created_at", "updated_at"]


class AdminShippingMethodSerializer(serializers.ModelSerializer):
    zone_public_ids = serializers.SlugRelatedField(
        many=True,
        required=False,
        slug_field="public_id",
        queryset=ShippingZone.objects.all(),
        source="zones",
    )

    class Meta:
        model = ShippingMethod
        fields = [
            "public_id",
            "name",
            "method_type",
            "is_active",
            "order",
            "zone_public_ids",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "created_at", "updated_at"]


class AdminShippingRateSerializer(serializers.ModelSerializer):
    shipping_method = serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=ShippingMethod.objects.all(),
    )
    shipping_zone = serializers.SlugRelatedField(
        slug_field="public_id",
        queryset=ShippingZone.objects.all(),
    )

    class Meta:
        model = ShippingRate
        fields = [
            "public_id",
            "shipping_method",
            "shipping_zone",
            "rate_type",
            "min_order_total",
            "max_order_total",
            "price",
            "is_active",
        ]
        read_only_fields = ["public_id"]

