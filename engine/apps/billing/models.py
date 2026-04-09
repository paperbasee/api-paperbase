from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from engine.core.ids import generate_public_id
from engine.utils.time import bd_today


class Plan(models.Model):
    """Subscription plan defining limits and features."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. pln_xxx).",
    )

    class BillingCycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    name = models.CharField(max_length=60)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    billing_cycle = models.CharField(
        max_length=20,
        choices=BillingCycle.choices,
        default=BillingCycle.MONTHLY,
    )
    features = models.JSONField(
        default=dict,
        blank=True,
        help_text='Structured config: {"limits": {"max_products": N}, "features": {"basic_analytics": bool, ...}}',
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Marketing/display only. Does not grant dashboard access.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["price"]
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("plan")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.name} ({self.get_billing_cycle_display()})"


class Subscription(models.Model):
    """User subscription to a plan. Only one active subscription per user at a time."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. sub_xxx).",
    )

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        CANCELED = "canceled", "Canceled"

    class BillingCycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    class Source(models.TextChoices):
        PAYMENT = "payment", "Payment"
        MANUAL = "manual", "Manual"
        TRIAL = "trial", "Trial"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    billing_cycle = models.CharField(
        max_length=20,
        choices=BillingCycle.choices,
        default=BillingCycle.MONTHLY,
    )
    start_date = models.DateField()
    end_date = models.DateField()
    auto_renew = models.BooleanField(default=False)
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["user", "status"]),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("subscription")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} - {self.plan} ({self.status})"

    def clean(self) -> None:
        if self.status == self.Status.ACTIVE:
            existing = Subscription.objects.filter(
                user=self.user,
                status=self.Status.ACTIVE,
            ).exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError(
                    {"status": "User can only have one active subscription at a time."}
                )

    def is_active(self) -> bool:
        today = bd_today()
        return self.status == self.Status.ACTIVE and self.end_date >= today

    def days_remaining(self) -> int:
        if not self.is_active():
            return 0
        today = bd_today()
        return (self.end_date - today).days


class Payment(models.Model):
    """Payment record for subscription or other charges. Stores all attempts including failed."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. pay_xxx).",
    )

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    class Provider(models.TextChoices):
        STRIPE = "stripe", "Stripe"
        PADDLE = "paddle", "Paddle"
        SSLCOMMERZ = "sslcommerz", "SSLCommerz"
        MANUAL = "manual", "Manual"
        BKASH = "bkash", "bKash"
        NAGAD = "nagad", "Nagad"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pending_payments",
        help_text="Intended plan for this payment. Set during initiation; cleared when subscription is linked.",
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="payments",
        null=True,
        blank=True,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="BDT")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
    )
    provider = models.CharField(
        max_length=30,
        choices=Provider.choices,
    )
    transaction_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="Provider transaction ID. Null for manual payments.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw provider response or additional data.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["provider"]),
            models.Index(fields=["created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("payment")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} - {self.amount} {self.currency} ({self.status})"
