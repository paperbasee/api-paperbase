from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from engine.apps.stores.models import Store
from engine.core.ids import generate_public_id
from engine.core.media_upload_paths import tenant_blog_featured_image_upload_to


class BlogTag(models.Model):
    """Store-scoped blog tag."""

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. btg_xxx).",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="blog_tags",
    )
    name = models.CharField(max_length=64)
    slug = models.SlugField(max_length=80, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "slug"],
                name="uniq_blogtag_store_slug",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("blogtag")
        base_slug = slugify((self.name or "").strip())[:80] or "tag"
        self.slug = _unique_slug(
            BlogTag, store_id=self.store_id, base_slug=base_slug, max_length=80, instance_pk=self.pk
        )
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Blog(models.Model):
    """Store-scoped blog post; visible on storefront when `published_at` is set."""

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. blg_xxx).",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="blogs",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blogs",
    )

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, blank=True, default="")
    content = models.TextField(blank=True)
    excerpt = models.CharField(max_length=500, blank=True)

    featured_image = models.ImageField(
        upload_to=tenant_blog_featured_image_upload_to,
        blank=True,
        null=True,
    )

    meta_title = models.CharField(max_length=255, blank=True)
    meta_description = models.CharField(max_length=500, blank=True)

    tags = models.ManyToManyField(BlogTag, blank=True, related_name="blogs")

    published_at = models.DateTimeField(null=True, blank=True, db_index=True)

    is_featured = models.BooleanField(default=False)
    is_public = models.BooleanField(default=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    views = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "slug"],
                name="uniq_blog_store_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["slug"], name="blog_slug_idx"),
            models.Index(fields=["published_at"], name="blog_published_at_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("blog")
        base_source = (self.title or "").strip()
        base_slug = slugify(base_source)[:255] or "post"
        self.slug = _unique_slug(
            Blog,
            store_id=self.store_id,
            base_slug=base_slug,
            max_length=255,
            instance_pk=self.pk,
        )
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title or f"Blog {self.public_id}"

    def get_media_keys(self) -> list[str]:
        key = getattr(self.featured_image, "name", "") if self.featured_image else ""
        return [key] if key else []

    def soft_delete(self) -> None:
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at", "updated_at"])


def _unique_slug(model_cls, *, store_id, base_slug: str, max_length: int, instance_pk) -> str:
    """
    Return a slug unique per (store, slug). Appends -2, -3, ... suffixes as needed,
    truncating the base to fit max_length. Matches the pattern used by Category.
    """
    if not store_id:
        return base_slug[:max_length]
    qs = model_cls.objects.filter(store_id=store_id)
    if instance_pk:
        qs = qs.exclude(pk=instance_pk)
    candidate = base_slug[:max_length]
    counter = 2
    original = candidate
    while qs.filter(slug=candidate).exists():
        suffix = f"-{counter}"
        head_len = max(1, max_length - len(suffix))
        candidate = (original[:head_len].rstrip("-") or "x") + suffix
        counter += 1
    return candidate
