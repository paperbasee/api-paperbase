from django.db import models

from engine.apps.stores.models import Store
from engine.core.ids import generate_public_id


class MarketingIntegration(models.Model):
    """Third-party marketing integration credentials scoped to a store."""

    class Provider(models.TextChoices):
        FACEBOOK = "facebook", "Facebook"
        GOOGLE_ANALYTICS = "google_analytics", "Google Analytics"
        TIKTOK = "tiktok", "TikTok"

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="marketing_integrations",
    )
    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. mkt_xxx).",
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    pixel_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Tracking pixel / measurement ID (e.g. Facebook Pixel ID).",
    )
    access_token_encrypted = models.TextField(
        blank=True,
        default="",
        help_text="Fernet-encrypted access token.",
    )
    test_event_code = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Optional test event code for validation (Facebook).",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "provider"],
                name="uniq_marketingintegration_store_provider",
            )
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("mktintegration")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_provider_display()} – {self.store.name}"


class IntegrationEventSettings(models.Model):
    """Controls which events are tracked for a marketing integration."""

    integration = models.OneToOneField(
        MarketingIntegration,
        on_delete=models.CASCADE,
        related_name="event_settings",
    )
    # Meta standard events only (no legacy/custom event names).
    track_purchase = models.BooleanField(default=True)
    track_initiate_checkout = models.BooleanField(default=True)
    track_add_to_cart = models.BooleanField(default=True)
    track_view_content = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Integration event settings"

    def __str__(self):
        return f"Event settings for {self.integration}"


class StoreEventLog(models.Model):
    """
    Tenant-scoped structured event log for low-volume, high-signal integrations.

    Designed for operational debugging and auditability; NOT a full analytics stream.
    """

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="event_logs",
    )
    app = models.CharField(max_length=50, db_index=True)
    event_type = models.CharField(max_length=80, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, db_index=True)
    message = models.CharField(max_length=500, blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["store", "created_at"]),
            models.Index(fields=["store", "event_type", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.store_id} {self.event_type} {self.status}"
