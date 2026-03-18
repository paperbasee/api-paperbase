from django.db import models
from django.conf import settings


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
