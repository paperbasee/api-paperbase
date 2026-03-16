import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from products.models import Product


class OrderNumberCounter(models.Model):
    """Single-row table for atomic sequential order number generation."""
    id = models.PositiveIntegerField(primary_key=True, default=1)
    next_value = models.PositiveBigIntegerField(default=1)


class Order(models.Model):
    """Order for checkout and track-order."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        CANCELLED = 'cancelled', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(
        max_length=20, unique=True, db_index=True, editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='orders'
    )
    # Email is optional; checkout is phone-based for guests.
    email = models.EmailField(blank=True, default='')
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    # Snapshot of shipping for display
    shipping_name = models.CharField(max_length=255, blank=True)
    shipping_address = models.TextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    class DeliveryArea(models.TextChoices):
        INSIDE = 'inside', 'Inside Dhaka City'
        OUTSIDE = 'outside', 'Outside Dhaka City'

    delivery_area = models.CharField(
        max_length=50,
        choices=DeliveryArea.choices,
        default=DeliveryArea.INSIDE,
        blank=True,
    )
    district = models.CharField(max_length=100, blank=True, default='')
    tracking_number = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        display_id = self.order_number or str(self.id)[:8]
        return f"Order {display_id}"


class OrderItem(models.Model):
    """Line item in an order with price snapshot."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    size = models.CharField(max_length=20, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.order} - {self.product.name} x{self.quantity}"
