from rest_framework import serializers
from .models import ShippingMethod, ShippingZone, ShippingRate


class ShippingOptionSerializer(serializers.Serializer):
    method_public_id = serializers.CharField()
    method_name = serializers.CharField()
    zone_public_id = serializers.CharField()
    zone_name = serializers.CharField()
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    rate_type = serializers.CharField()
