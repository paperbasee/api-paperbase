import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from engine.apps.customers.models import Customer
from engine.core.ids import generate_public_id
from engine.apps.products.models import Product
from engine.apps.stores.models import Store
from engine.apps.shipping.models import ShippingMethod, ShippingRate, ShippingZone


class OrderNumberCounter(models.Model):
    """Single-row table for atomic sequential order number generation."""
    store = models.OneToOneField(
        Store,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="order_counter",
    )
    next_value = models.PositiveBigIntegerField(default=1)


class Order(models.Model):
    """Order for checkout and track-order. Status lifecycle: Pending -> Confirmed -> Processing -> Shipped -> Delivered (or Cancelled/Returned)."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        PROCESSING = 'processing', 'Processing'
        SHIPPED = 'shipped', 'Shipped'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'
        RETURNED = 'returned', 'Returned'

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. ord_xxx).",
    )
    order_number = models.CharField(
        max_length=20, unique=True, db_index=True, editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='orders'
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders",
    )
    email = models.EmailField(blank=True, default='')
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    shipping_zone = models.ForeignKey(
        ShippingZone,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    shipping_method = models.ForeignKey(
        ShippingMethod,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )
    shipping_rate = models.ForeignKey(
        ShippingRate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )
    shipping_name = models.CharField(max_length=255, blank=True)
    shipping_address = models.TextField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    district = models.CharField(max_length=100, blank=True, default='')
    tracking_number = models.CharField(max_length=100, blank=True)
    courier_provider = models.CharField(max_length=20, blank=True, default="")
    courier_consignment_id = models.CharField(max_length=100, blank=True, default="")
    courier_tracking_code = models.CharField(max_length=100, blank=True, default="")
    courier_status = models.CharField(max_length=50, blank=True, default="")
    sent_to_courier = models.BooleanField(default=False)
    customer_confirmation_sent_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Set when ORDER_CONFIRMED was sent to the customer (send-to-courier).",
    )
    extra_data = models.JSONField(
        blank=True,
        default=dict,
        help_text="Dynamic extra fields per extra_field_schema.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("order")
        # Ensure admin-created orders also get an order_number.
        if not self.order_number:
            from .utils import get_next_order_number

            self.order_number = get_next_order_number(self.store)
        super().save(*args, **kwargs)

    def __str__(self):
        display_id = self.order_number or str(self.id)[:8]
        return f"Order {display_id}"


class OrderAddress(models.Model):
    """Shipping or billing address snapshot for an order."""

    class AddressType(models.TextChoices):
        SHIPPING = 'shipping', 'Shipping'
        BILLING = 'billing', 'Billing'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='addresses')
    address_type = models.CharField(max_length=20, choices=AddressType.choices)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, blank=True)
    address_line1 = models.CharField(max_length=255)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100)

    class Meta:
        ordering = ['order', 'address_type']
        unique_together = [['order', 'address_type']]

    def __str__(self):
        return f"{self.order.order_number} - {self.get_address_type_display()}"


class OrderStatusHistory(models.Model):
    """Audit trail of order status changes."""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='status_history')
    status = models.CharField(max_length=20, choices=Order.Status.choices)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Order status history'

    def __str__(self):
        return f"{self.order.order_number} -> {self.status}"


class OrderItem(models.Model):
    """Line item in an order with price snapshot."""
    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. oit_xxx).",
    )
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    variant = models.ForeignKey(
        'products.ProductVariant',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order_items',
    )
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("orderitem")
        super().save(*args, **kwargs)

    def __str__(self):
        product_name = self.product.name if self.product else "Unavailable"
        return f"{self.order} - {product_name} x{self.quantity}"
