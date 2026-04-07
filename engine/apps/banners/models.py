from django.db import models
from django.core.exceptions import ValidationError

from engine.apps.stores.models import Store
from engine.core.ids import generate_public_id
from engine.core.media_upload_paths import tenant_banner_image_upload_to


class Banner(models.Model):
    """Store-scoped promotional banner for the storefront (image + optional CTA)."""

    PLACEMENT_CHOICES = [
        ("global_topbar", "Global Topbar"),
        ("global_bottom", "Global Bottom"),
        ("home_top", "Home Top"),
        ("home_mid", "Home Mid"),
        ("home_bottom", "Home Bottom"),
        ("dashboard_header", "Dashboard Header"),
        ("dashboard_sidebar", "Dashboard Sidebar"),
        ("dashboard_mid", "Dashboard Mid"),
        ("product_top", "Product Top"),
        ("product_mid", "Product Mid"),
        ("product_bottom", "Product Bottom"),
        ("checkout_top", "Checkout Top"),
        ("checkout_bottom", "Checkout Bottom"),
    ]

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. ban_xxx).",
    )

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="banners",
    )
    title = models.CharField(max_length=255, blank=True)
    image = models.ImageField(upload_to=tenant_banner_image_upload_to)
    cta_text = models.CharField(max_length=255, blank=True)
    cta_link = models.URLField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    placement_slots = models.JSONField(default=list)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("banner")
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.title or f"Banner {self.public_id}"

    def clean(self):
        super().clean()
        slots = self.placement_slots
        if not slots or len(slots) == 0:
            raise ValidationError({"placement_slots": "At least one placement slot is required"})
        allowed = {k for k, _ in self.PLACEMENT_CHOICES}
        invalid = [p for p in slots if p not in allowed]
        if invalid:
            raise ValidationError({"placement_slots": "Invalid placement slot selected"})

    def get_media_keys(self) -> list[str]:
        key = getattr(self.image, "name", "") if self.image else ""
        return [key] if key else []
