from rest_framework import serializers

from engine.core.media_urls import absolute_media_url
from engine.core.serializers import SafeModelSerializer

from .models import Banner


class PublicBannerSerializer(SafeModelSerializer):
    """Storefront payload: absolute image URL, consistent CTA and schedule fields."""

    image_url = serializers.SerializerMethodField()
    cta_url = serializers.CharField(source="cta_link", read_only=True, allow_blank=True)
    start_at = serializers.SerializerMethodField()
    end_at = serializers.SerializerMethodField()

    class Meta:
        model = Banner
        fields = [
            "public_id",
            "title",
            "image_url",
            "cta_text",
            "cta_url",
            "order",
            "start_at",
            "end_at",
        ]

    def get_image_url(self, obj: Banner) -> str | None:
        return absolute_media_url(obj.image, self.context.get("request"))

    def get_start_at(self, obj: Banner) -> str | None:
        return obj.start_at.isoformat() if obj.start_at else None

    def get_end_at(self, obj: Banner) -> str | None:
        return obj.end_at.isoformat() if obj.end_at else None
