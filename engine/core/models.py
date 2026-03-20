from django.db import models
from django.conf import settings

from .ids import generate_public_id


class PublicIdMixin(models.Model):
    """
    Abstract mixin that adds a prefixed opaque `public_id` field and auto-populates
    it on first save.  Subclasses must define `PUBLIC_ID_KIND` matching a key in
    `engine.core.ids._PREFIXES` and call super().save() from their own save().

    Usage::

        class MyModel(PublicIdMixin, models.Model):
            PUBLIC_ID_KIND = "mymodel"
            ...

            def save(self, *args, **kwargs):
                # PublicIdMixin.save() populates public_id before persisting
                super().save(*args, **kwargs)
    """

    PUBLIC_ID_KIND: str = ""

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. prd_xxx).",
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.public_id and self.PUBLIC_ID_KIND:
            self.public_id = generate_public_id(self.PUBLIC_ID_KIND)
        super().save(*args, **kwargs)


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
    store = models.ForeignKey(
        "stores.Store",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. act_xxx).",
    )
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

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("activitylog")
        super().save(*args, **kwargs)
