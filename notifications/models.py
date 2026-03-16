from django.db import models


class Notification(models.Model):
    """Notification banner for frontend display."""

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
