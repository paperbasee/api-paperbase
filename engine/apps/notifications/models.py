from django.conf import settings
from django.db import models
from django.db.models import F, OuterRef, Q, Subquery
from django.utils import timezone

from engine.apps.stores.models import Store
from engine.core.ids import generate_public_id


class StaffNotification(models.Model):
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
        SUPPORT_TICKET = 'support_ticket', 'Support ticket submitted'
        OTHER = 'other', 'Other'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='staff_notifications',
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
            self.public_id = generate_public_id("staffnotification")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_message_type_display()}: {self.title}"


class PlatformNotification(models.Model):
    """
    Global platform notification for the dashboard banner (not store- or user-scoped).
    Managed in Django Admin only; exposed read-only via API for authenticated dashboard users.
    """
    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. sys_xxx).",
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    cta_text = models.CharField(max_length=100, blank=True, null=True)
    cta_url = models.URLField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField(blank=True, null=True)
    priority = models.IntegerField(
        default=0,
        help_text="Higher values win when multiple notifications are active.",
    )
    daily_limit = models.IntegerField(
        default=3,
        help_text="How many times a user must dismiss before hiding for the day",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-priority', '-created_at']

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("systemnotification")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    @property
    def is_currently_active(self) -> bool:
        if not self.is_active:
            return False
        now = timezone.now()
        if now < self.start_at:
            return False
        if self.end_at is not None and now > self.end_at:
            return False
        return True

    @classmethod
    def active_queryset(cls, now=None):
        """Notifications visible at ``now`` (default: timezone.now())."""
        if now is None:
            now = timezone.now()
        return cls.objects.filter(
            is_active=True,
            start_at__lte=now,
        ).filter(
            models.Q(end_at__isnull=True) | models.Q(end_at__gte=now),
        )

    @classmethod
    def visible_for_user_queryset(cls, user, now=None):
        """
        Active notifications the user has not dismissed past ``daily_limit`` today.
        Uses the active timezone for the calendar ``date`` on NotificationDismissal rows.
        """
        if now is None:
            now = timezone.now()
        today = timezone.localtime(now).date()
        view_today = NotificationDismissal.objects.filter(
            user=user,
            notification_id=OuterRef("pk"),
            date=today,
        ).values("dismiss_count")[:1]
        return (
            cls.active_queryset(now)
            .annotate(today_dismiss=Subquery(view_today))
            .filter(Q(today_dismiss__isnull=True) | Q(today_dismiss__lt=F("daily_limit")))
            .order_by("-priority", "-created_at")
        )


class NotificationDismissal(models.Model):
    """Per-user daily dismiss tracking for global :class:`PlatformNotification` rows."""

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. ntv_xxx).",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="platform_notifications",
    )
    notification = models.ForeignKey(
        "PlatformNotification",
        on_delete=models.CASCADE,
        related_name="notification_dismissals",
    )
    date = models.DateField()
    dismiss_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-date", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "notification", "date"],
                name="notifications_notifdismiss_user_notif_date_uniq",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("systemnotificationview")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.notification_id} / {self.user_id} @ {self.date}"


class StorefrontCTA(models.Model):
    """Store-scoped notification banner for storefront / dashboard CTA management."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. cta_xxx).",
    )

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="storefront_ctas",
    )

    class NotificationType(models.TextChoices):
        BANNER = 'banner', 'Banner'
        ALERT = 'alert', 'Alert'
        PROMO = 'promo', 'Promotion'

    cta_text = models.CharField(max_length=500)
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
        return f"{self.cta_text[:50]}... ({'Active' if self.is_active else 'Inactive'})"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("notification")
        super().save(*args, **kwargs)

    @property
    def is_currently_active(self):
        """Check if notification is active and within date range."""
        if not self.is_active:
            return False
        now = timezone.now()
        if self.start_date and now < self.start_date:
            return False
        if self.end_date and now > self.end_date:
            return False
        return True
