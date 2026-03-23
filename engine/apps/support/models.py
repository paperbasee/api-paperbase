from django.db import models

from engine.apps.stores.models import Store
from engine.core.ids import generate_public_id


def support_attachment_upload_to(instance: "SupportTicketAttachment", filename: str) -> str:
    store_pub = getattr(instance.ticket.store, "public_id", "unknown")
    ticket_pub = getattr(instance.ticket, "public_id", "unknown")
    return f"store_{store_pub}/support/tickets/{ticket_pub}/{filename}"


class SupportTicket(models.Model):
    """Support ticket submitted by a store visitor/customer."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. tkt_xxx).",
    )

    class Status(models.TextChoices):
        NEW = "new", "New"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Category(models.TextChoices):
        GENERAL = "general", "General"
        ORDER = "order", "Order"
        PAYMENT = "payment", "Payment"
        SHIPPING = "shipping", "Shipping"
        PRODUCT = "product", "Product"
        TECHNICAL = "technical", "Technical"
        OTHER = "other", "Other"

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="support_tickets")
    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20, blank=True, default="")
    subject = models.CharField(max_length=255, blank=True, default="")
    message = models.TextField()
    order_number = models.CharField(max_length=64, blank=True, default="")
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.GENERAL)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    internal_notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["store", "-created_at"]),
            models.Index(fields=["store", "status", "-created_at"]),
            models.Index(fields=["store", "priority", "-created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("ticket")
        super().save(*args, **kwargs)

    def __str__(self):
        base = self.subject or "Support ticket"
        return f"{base} - {self.name}"


class SupportTicketAttachment(models.Model):
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. ath_xxx).",
    )
    ticket = models.ForeignKey(
        SupportTicket,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to=support_attachment_upload_to)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("attachment")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Attachment for ticket {self.ticket_id}"
