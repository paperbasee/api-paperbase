from django.db import models

from engine.apps.stores.models import Store
from engine.core.ids import generate_public_id


class Courier(models.Model):
    """Third-party courier integration credentials scoped to a store."""

    class Provider(models.TextChoices):
        PATHAO = "pathao", "Pathao"
        STEADFAST = "steadfast", "Steadfast"

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="couriers",
    )
    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. crr_xxx).",
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    api_key_encrypted = models.TextField(
        help_text="Fernet-encrypted API key.",
    )
    secret_key_encrypted = models.TextField(
        blank=True,
        default="",
        help_text="Fernet-encrypted secret key (Steadfast).",
    )
    access_token_encrypted = models.TextField(
        blank=True,
        default="",
        help_text="Fernet-encrypted access/bearer token (Pathao).",
    )
    refresh_token = models.TextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("courier")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_provider_display()} – {self.store.name}"
