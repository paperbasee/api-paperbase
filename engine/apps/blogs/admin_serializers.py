from rest_framework import serializers

from engine.core.media_deletion_service import schedule_media_deletion_from_keys
from engine.core.media_urls import absolute_media_url
from engine.core.serializers import SafeModelSerializer

from .models import Blog, BlogTag


def _truthy_flag(raw) -> bool:
    return raw is True or (
        isinstance(raw, str) and raw.strip().lower() in ("true", "1")
    )


class AdminBlogTagSerializer(SafeModelSerializer):
    class Meta:
        model = BlogTag
        fields = ["public_id", "name", "slug", "created_at"]
        read_only_fields = ["public_id", "slug", "created_at"]


class _BlogMiniTagSerializer(SafeModelSerializer):
    class Meta:
        model = BlogTag
        fields = ["public_id", "name", "slug"]


class AdminBlogSerializer(SafeModelSerializer):
    """Dashboard CRUD serializer — public_id-only external references."""

    featured_image_url = serializers.SerializerMethodField(read_only=True)
    remove_featured_image = serializers.CharField(
        write_only=True, required=False, allow_blank=True
    )
    tags = _BlogMiniTagSerializer(many=True, read_only=True)

    tag_public_ids = serializers.ListField(
        child=serializers.CharField(allow_blank=True),
        write_only=True,
        required=False,
        allow_empty=True,
    )
    clear_tags = serializers.CharField(write_only=True, required=False, allow_blank=True)

    author_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Blog
        fields = [
            "public_id",
            "title",
            "slug",
            "content",
            "excerpt",
            "featured_image",
            "remove_featured_image",
            "featured_image_url",
            "meta_title",
            "meta_description",
            "tags",
            "tag_public_ids",
            "clear_tags",
            "published_at",
            "is_featured",
            "is_public",
            "views",
            "author_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "public_id",
            "slug",
            "published_at",
            "views",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "featured_image": {"required": False, "allow_null": True},
        }

    def get_featured_image_url(self, obj: Blog) -> str | None:
        return absolute_media_url(obj.featured_image, self.context.get("request"))

    def get_author_name(self, obj: Blog) -> str:
        author = obj.author
        if author is None:
            return ""
        full = f"{author.first_name or ''} {author.last_name or ''}".strip()
        return full or (getattr(author, "email", "") or "")

    def validate_tag_public_ids(self, value):
        if value is None:
            return []
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return []
            value = [s]
        elif hasattr(value, "getlist"):
            value = value.getlist("tag_public_ids")
        elif not isinstance(value, (list, tuple)):
            raise serializers.ValidationError("Invalid tags payload.")
        store_id = (self.context or {}).get("store_id")
        if store_id is None:
            raise serializers.ValidationError("Store context missing.")
        normalized = [str(v).strip() for v in value if str(v or "").strip()]
        if not normalized:
            return []
        tags = list(BlogTag.objects.filter(store_id=store_id, public_id__in=normalized))
        found = {t.public_id for t in tags}
        missing = [v for v in normalized if v not in found]
        if missing:
            raise serializers.ValidationError(f"Tag(s) not found: {', '.join(missing)}")
        return tags

    def validate(self, attrs):
        # Multipart parsers may pass "" for cleared file fields; normalize before save.
        if attrs.get("featured_image") == "":
            attrs["featured_image"] = None
        return attrs

    def _pop_relations(self, validated):
        tags = validated.pop("tag_public_ids", None)
        clear_tags = _truthy_flag(validated.pop("clear_tags", None))
        return tags, clear_tags

    def create(self, validated_data):
        remove_raw = validated_data.pop("remove_featured_image", None)
        remove_flag = _truthy_flag(remove_raw)
        image_provided = "featured_image" in validated_data
        incoming_image = validated_data.get("featured_image") if image_provided else None
        if remove_flag and (not image_provided or incoming_image is None):
            validated_data["featured_image"] = None
        tags, clear_tags = self._pop_relations(validated_data)
        instance = super().create(validated_data)
        if clear_tags:
            instance.tags.clear()
        elif tags:
            instance.tags.set(tags)
        return instance

    def update(self, instance, validated_data):
        remove_raw = validated_data.pop("remove_featured_image", None)
        remove_flag = _truthy_flag(remove_raw)

        old_key = (
            instance.featured_image.name
            if instance.featured_image and getattr(instance.featured_image, "name", None)
            else None
        )
        image_provided = "featured_image" in validated_data
        incoming_image = validated_data.get("featured_image") if image_provided else None

        if remove_flag and not image_provided:
            if old_key:
                schedule_media_deletion_from_keys([old_key])
            validated_data["featured_image"] = None
        elif image_provided and incoming_image is not None and old_key:
            schedule_media_deletion_from_keys([old_key])

        tags, clear_tags = self._pop_relations(validated_data)
        instance = super().update(instance, validated_data)
        if clear_tags:
            instance.tags.clear()
        elif tags is not None:
            instance.tags.set(tags)
        return instance
