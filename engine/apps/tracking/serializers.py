from __future__ import annotations

from urllib.parse import urlparse

from rest_framework import serializers

from engine.apps.tracking.contract import ALLOWED_EVENT_NAMES


class TrackingEventIngestSerializer(serializers.Serializer):
    """
    Strict ingestion schema for tracker.js -> Django.

    IMPORTANT:
    - event_id is accepted as-is (never generated or modified server-side)
    - event_time is client-side unix seconds (int)
    """

    event_name = serializers.ChoiceField(choices=sorted(ALLOWED_EVENT_NAMES))
    # event_id must be accepted as-is for Meta deduplication (no trimming/mutation).
    event_id = serializers.CharField(min_length=1, max_length=255, allow_blank=False, trim_whitespace=False)
    event_time = serializers.IntegerField(min_value=1)
    event_source_url = serializers.CharField(min_length=8, max_length=2048, allow_blank=False, trim_whitespace=True)

    value = serializers.FloatField(required=False, default=0.0)
    currency = serializers.CharField(required=False, default="BDT", max_length=8, allow_blank=False, trim_whitespace=True)
    content_type = serializers.CharField(required=False, default="product", max_length=50, allow_blank=False, trim_whitespace=True)
    content_ids = serializers.ListField(
        child=serializers.CharField(max_length=128, allow_blank=False, trim_whitespace=True),
        required=False,
        default=list,
        allow_empty=True,
        max_length=200,
    )

    fbp = serializers.CharField(required=False, allow_null=True, default=None, max_length=512, allow_blank=False)
    fbc = serializers.CharField(required=False, allow_null=True, default=None, max_length=512, allow_blank=False)
    user_agent = serializers.CharField(min_length=1, max_length=512, allow_blank=False, trim_whitespace=False)
    extra = serializers.DictField(required=False, default=dict)

    def validate_event_source_url(self, value: str) -> str:
        raw = (value or "").strip()
        parts = urlparse(raw)
        if parts.scheme not in {"http", "https"}:
            raise serializers.ValidationError("event_source_url must be http(s).")
        if not parts.netloc:
            raise serializers.ValidationError("event_source_url must include a host.")
        return raw

    def validate_event_id(self, value: str) -> str:
        # CharField already enforces string type; we additionally enforce that
        # event_id is not altered by server-side trimming.
        if value is None or value == "":
            raise serializers.ValidationError("event_id is required.")
        if value != value.strip():
            raise serializers.ValidationError("event_id must not contain leading/trailing whitespace.")
        # Do not enforce a stricter pattern here; tracker.js owns the format.
        return value

    def validate_fbp(self, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise serializers.ValidationError("fbp must be a string or null.")
        return value

    def validate_fbc(self, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise serializers.ValidationError("fbc must be a string or null.")
        return value

    def validate_extra(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("extra must be an object.")
        return value

