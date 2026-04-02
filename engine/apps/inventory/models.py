from django.conf import settings
from django.db import models
from django.db.models import Q

from engine.core.ids import generate_public_id
from .utils import clamp_stock


class Inventory(models.Model):
    """
    Stock tracking for a product or a product variant.
    When variant is null, this tracks the product's own stock (simple products).
    When variant is set, this tracks that variant's stock.
    """
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. inv_xxx).",
    )
    product = models.ForeignKey(
        'products.Product',
        on_delete=models.CASCADE,
        related_name='inventory_records',
    )
    variant = models.OneToOneField(
        'products.ProductVariant',
        on_delete=models.CASCADE,
        related_name='inventory',
        null=True,
        blank=True,
        help_text="Null for product-level stock; set for variant-level stock.",
    )
    quantity = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(
        default=5,
        help_text="Alert when quantity falls at or below this value.",
    )
    is_tracked = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Inventories'
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'variant'],
                name='inventory_product_variant_unique',
            ),
            models.UniqueConstraint(
                fields=['product'],
                condition=Q(variant__isnull=True),
                name='unique_product_level_inventory',
            ),
        ]

    def __str__(self):
        if self.variant_id:
            return f"{self.product.name} / {self.variant.sku or self.variant_id}: {self.quantity}"
        return f"{self.product.name}: {self.quantity}"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("inventory")
        self.quantity = clamp_stock(self.quantity)
        super().save(*args, **kwargs)

    def is_low_stock(self):
        return self.is_tracked and self.quantity <= self.low_stock_threshold


class StockMovement(models.Model):
    """Record of a stock adjustment for auditing and history."""
    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. stm_xxx).",
    )

    class Reason(models.TextChoices):
        ADJUSTMENT = 'adjustment', 'Manual adjustment'
        SALE = 'sale', 'Sale / order'
        RETURN = 'return', 'Return / refund'
        RESTOCK = 'restock', 'Restock'
        DAMAGED = 'damaged', 'Damaged / lost'
        OTHER = 'other', 'Other'

    class Source(models.TextChoices):
        ORDER = "order", "Order lifecycle"
        ADMIN = "admin", "Admin inventory update"
        SYSTEM = "system", "System process"

    inventory = models.ForeignKey(
        Inventory,
        on_delete=models.CASCADE,
        related_name='movements',
    )
    change = models.IntegerField(help_text="Positive for increase, negative for decrease.")
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.ADJUSTMENT)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.SYSTEM)
    reference_id = models.CharField(max_length=100, blank=True, default="")
    reference = models.CharField(max_length=255, blank=True, help_text="e.g. order number, note")
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements',
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        sign = '+' if self.change >= 0 else ''
        return f"{self.inventory} {sign}{self.change} ({self.get_reason_display()})"

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("stockmovement")
        super().save(*args, **kwargs)
