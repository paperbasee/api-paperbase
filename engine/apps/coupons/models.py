from django.db import models

from engine.apps.stores.models import Store
from engine.core.ids import generate_public_id


class Coupon(models.Model):
    """Discount coupon for promotions."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. cpn_xxx).",
    )

    class DiscountType(models.TextChoices):
        PERCENTAGE = "percentage", "Percentage"
        FIXED = "fixed", "Fixed amount"

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="coupons",
    )
    code = models.CharField(max_length=50, db_index=True)
    discount_type = models.CharField(
        max_length=20,
        choices=DiscountType.choices,
    )
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    min_order_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    times_used = models.PositiveIntegerField(default=0)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["store", "code"],
                name="uniq_coupon_store_code",
            )
        ]
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("coupon")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} ({self.get_discount_type_display()})"
