from django.db import models
from django.conf import settings


class DashboardBranding(models.Model):
    """Singleton model for dashboard sidebar branding (logo, admin name, subtitle, currency)."""

    logo = models.ImageField(upload_to="branding/", blank=True, null=True)
    admin_name = models.CharField(max_length=100, default="Gadzilla")
    admin_subtitle = models.CharField(max_length=200, default="Admin dashboard")
    currency_symbol = models.CharField(max_length=10, default="৳", blank=True)

    class Meta:
        verbose_name = "Dashboard branding"
        verbose_name_plural = "Dashboard branding"

    def __str__(self):
        return self.admin_name or "Dashboard branding"


class ActivityLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        CUSTOM = "custom", "Custom"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_activity_logs",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    entity_type = models.CharField(max_length=50)
    entity_id = models.CharField(max_length=64, blank=True, default="")
    summary = models.CharField(max_length=255)
    metadata = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["entity_type", "action", "-created_at"]),
        ]

    def __str__(self) -> str:
        base = f"{self.entity_type}:{self.entity_id}" if self.entity_id else self.entity_type
        return f"{self.get_action_display()} {base}"
