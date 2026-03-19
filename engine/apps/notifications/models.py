from django.conf import settings
from django.db import models

from engine.core.ids import generate_public_id


class SystemNotification(models.Model):
    """
    Admin dashboard notification for events (new order, low stock, new customer, etc.).
    When user is null, the notification is global for all staff.
    """
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. snt_xxx).",
    )

    class MessageType(models.TextChoices):
        NEW_ORDER = 'new_order', 'New order'
        NEW_CUSTOMER = 'new_customer', 'New customer'
        LOW_STOCK = 'low_stock', 'Product out of stock'
        WISHLIST_ADD = 'wishlist_add', 'Product added to wishlist'
        CONTACT_SUBMISSION = 'contact_submission', 'Contact form submitted'
        OTHER = 'other', 'Other'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='system_notifications',
        help_text="Null = visible to all staff",
    )
    message_type = models.CharField(max_length=30, choices=MessageType.choices, default=MessageType.OTHER)
    title = models.CharField(max_length=255)
    payload = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("sysnotification")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_message_type_display()}: {self.title}"


class Notification(models.Model):
    """Notification banner for frontend display."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. ntf_xxx).",
    )

    class NotificationType(models.TextChoices):
        BANNER = 'banner', 'Banner'
        ALERT = 'alert', 'Alert'
        PROMO = 'promo', 'Promotion'

    text = models.CharField(max_length=500)
    notification_type = models.CharField(
        max_length=20, choices=NotificationType.choices, default=NotificationType.BANNER
    )
    is_active = models.BooleanField(default=True)
    link = models.URLField(blank=True, null=True)
    link_text = models.CharField(max_length=100, blank=True)
    # Optional scheduling
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0, help_text='Display order (lower = first)')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', '-created_at']

    def __str__(self):
        return f"{self.text[:50]}... ({'Active' if self.is_active else 'Inactive'})"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("notification")
        super().save(*args, **kwargs)

    @property
    def is_currently_active(self):
        """Check if notification is active and within date range."""
        if not self.is_active:
            return False
        from django.utils import timezone
        now = timezone.now()
        if self.start_date and now < self.start_date:
            return False
        if self.end_date and now > self.end_date:
            return False
        return True
