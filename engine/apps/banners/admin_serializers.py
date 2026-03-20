from rest_framework import serializers

from .models import Banner


class AdminBannerSerializer(serializers.ModelSerializer):
    redirect_url = serializers.URLField(required=False, allow_blank=True)

    class Meta:
        model = Banner
        fields = [
            "public_id",
            "image",
            "title",
            "description",
            "cta_text",
            "redirect_url",
            "is_clickable",
            "placement",
            "position",
            "is_active",
            "start_date",
            "end_date",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["public_id", "created_at", "updated_at"]

    def validate(self, attrs):
        is_clickable = attrs.get("is_clickable")
        redirect_url = attrs.get("redirect_url")

        if is_clickable is None and self.instance is not None:
            is_clickable = self.instance.is_clickable
        if redirect_url is None and self.instance is not None:
            redirect_url = self.instance.redirect_url

        if isinstance(redirect_url, str):
            redirect_url = redirect_url.strip()
            attrs["redirect_url"] = redirect_url

        if is_clickable and not redirect_url:
            raise serializers.ValidationError(
                {"redirect_url": "This field is required when banner is clickable."}
            )
        return attrs
