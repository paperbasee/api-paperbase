from rest_framework import serializers
from engine.core.serializers import SafeModelSerializer

from .models import Banner


class AdminBannerSerializer(SafeModelSerializer):
    cta_link = serializers.URLField(required=False, allow_blank=True)
    placement_slots = serializers.JSONField()

    class Meta:
        model = Banner
        fields = [
            "public_id",
            "image",
            "title",
            "cta_text",
            "cta_link",
            "is_active",
            "order",
            "placement_slots",
            "start_at",
            "end_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "created_at", "updated_at"]

    def validate_placement_slots(self, value):
        if not value or len(value) == 0:
            raise serializers.ValidationError("At least one placement slot is required")
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise serializers.ValidationError("Invalid placement slot selected")
        allowed = {k for k, _ in Banner.PLACEMENT_CHOICES}
        invalid = [p for p in value if p not in allowed]
        if invalid:
            raise serializers.ValidationError("Invalid placement slot selected")
        return value
