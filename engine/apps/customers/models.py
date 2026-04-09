from django.db import models
from django.db.models import Q
from decimal import Decimal

from engine.apps.stores.models import Store
from engine.core.ids import generate_public_id


class Customer(models.Model):
    """Per-store customer identity + aggregate rollups (no product-level data)."""
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. cus_xxx).",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="customers",
    )
    name = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=20)
    email = models.EmailField(null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    total_orders = models.PositiveIntegerField(default=0)
    total_spent = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    first_order_at = models.DateTimeField(null=True, blank=True)
    last_order_at = models.DateTimeField(null=True, blank=True)
    is_repeat_customer = models.BooleanField(default=False)
    avg_order_interval_days = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["store", "phone"],
                name="uniq_customer_store_phone",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("customer")
        super().save(*args, **kwargs)

    def __str__(self):
        if self.name:
            return self.name
        return self.phone
