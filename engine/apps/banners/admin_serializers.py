from rest_framework import serializers

from .models import Banner


class AdminBannerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Banner
        fields = [
            "id",
            "title",
            "image",
            "link_url",
            "position",
            "order",
            "is_active",
            "start_date",
            "end_date",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
