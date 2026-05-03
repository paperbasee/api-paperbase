from __future__ import annotations

from rest_framework import serializers

from .models import StorefrontTheme
from .presets import PALETTE_CHOICES, PALETTE_VERSION, resolve_palette


class StorefrontThemeSerializer(serializers.ModelSerializer):
    resolved_palette = serializers.SerializerMethodField(read_only=True)
    palette_version = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = StorefrontTheme
        fields = ("palette", "palette_version", "resolved_palette", "created_at", "updated_at")
        read_only_fields = ("created_at", "updated_at")

    def get_palette_version(self, obj: StorefrontTheme) -> str:
        return PALETTE_VERSION

    def get_resolved_palette(self, obj: StorefrontTheme) -> dict[str, str]:
        return resolve_palette(obj.palette)

    def validate_palette(self, value: str) -> str:
        key = (value or "").strip().lower()
        if key not in PALETTE_CHOICES:
            raise serializers.ValidationError("Invalid palette.")
        return key


def serialize_theme_payload(theme: StorefrontTheme) -> dict:
    """Serialized dict suitable for Redis cache and Response."""
    ser = StorefrontThemeSerializer(theme)
    return dict(ser.data)
