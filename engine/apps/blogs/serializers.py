from rest_framework import serializers

from engine.core.media_urls import absolute_media_url
from engine.core.serializers import SafeModelSerializer

from .models import Blog, BlogTag


class PublicBlogTagMiniSerializer(SafeModelSerializer):
    class Meta:
        model = BlogTag
        fields = ["public_id", "name", "slug"]


class PublicBlogListSerializer(SafeModelSerializer):
    """Storefront list payload — minimal public fields only."""

    featured_image_url = serializers.SerializerMethodField()
    published_at = serializers.SerializerMethodField()
    tags = PublicBlogTagMiniSerializer(many=True, read_only=True)

    class Meta:
        model = Blog
        fields = [
            "public_id",
            "title",
            "slug",
            "excerpt",
            "featured_image_url",
            "meta_title",
            "meta_description",
            "tags",
            "is_featured",
            "views",
            "published_at",
        ]

    def get_featured_image_url(self, obj: Blog) -> str | None:
        return absolute_media_url(obj.featured_image, self.context.get("request"))

    def get_published_at(self, obj: Blog) -> str | None:
        return obj.published_at.isoformat() if obj.published_at else None


class PublicBlogDetailSerializer(PublicBlogListSerializer):
    """Storefront detail payload — adds content + author name."""

    author_name = serializers.SerializerMethodField()

    class Meta(PublicBlogListSerializer.Meta):
        fields = PublicBlogListSerializer.Meta.fields + ["content", "author_name"]

    def get_author_name(self, obj: Blog) -> str:
        author = obj.author
        if author is None:
            return ""
        full = f"{author.first_name or ''} {author.last_name or ''}".strip()
        return full or (getattr(author, "email", "") or "")
