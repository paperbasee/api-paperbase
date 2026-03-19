from django.db import models

from engine.apps.stores.models import Store
from engine.core.ids import generate_public_id


class Banner(models.Model):
    """Banner for store frontend positions (homepage, sidebar, footer, etc.)."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. ban_xxx).",
    )

    class Position(models.TextChoices):
        HOMEPAGE = "homepage", "Homepage"
        SIDEBAR = "sidebar", "Sidebar"
        FOOTER = "footer", "Footer"
        HEADER = "header", "Header"

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="banners",
    )
    title = models.CharField(max_length=255, blank=True)
    image = models.ImageField(upload_to="banners/")
    link_url = models.URLField(max_length=500, blank=True)
    position = models.CharField(
        max_length=20,
        choices=Position.choices,
        db_index=True,
    )
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "order", "id"]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("banner")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title or 'Banner'} ({self.get_position_display()})"
