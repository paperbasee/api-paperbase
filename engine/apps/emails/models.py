from django.db import models

from engine.core.models import PublicIdMixin


class EmailTemplate(PublicIdMixin, models.Model):
    """
    Reusable transactional email definition. ``type`` is the lookup key (globally unique).
    """

    PUBLIC_ID_KIND = "emailtemplate"

    type = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Lookup key, e.g. ORDER_CONFIRMED, EMAIL_VERIFICATION.",
    )
    subject = models.CharField(max_length=255)
    html_body = models.TextField()
    text_body = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["type"]

    def __str__(self) -> str:
        return self.type


class EmailLog(PublicIdMixin, models.Model):
    """Audit trail for every outbound email attempt."""

    PUBLIC_ID_KIND = "emaillog"

    store = models.ForeignKey(
        "stores.Store",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="email_logs",
    )

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    to_email = models.EmailField()
    type = models.CharField(max_length=64, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    provider = models.CharField(max_length=32, default="resend")
    error_message = models.TextField(blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.type} → {self.to_email} ({self.status})"
