from decimal import Decimal

from django.db import models

from engine.apps.stores.models import Store


class ShippingZone(models.Model):
    """Region (Bangladesh-focused) for shipping rules."""
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="shipping_zones",
    )
    name = models.CharField(max_length=100)
    delivery_areas = models.CharField(
        max_length=100,
        blank=True,
        help_text="Comma-separated delivery areas (e.g. inside,outside). Blank = any.",
    )
    districts = models.CharField(
        max_length=500,
        blank=True,
        help_text="Comma-separated districts/cities this zone applies to (e.g. Dhaka,Chittagong). Blank = any.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=["store", "is_active", "name"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["store", "name"], name="uniq_shipping_zone_store_name"),
        ]

    def __str__(self):
        return self.name


class ShippingMethod(models.Model):
    """Carrier/method (e.g. standard, express, pickup)."""

    class MethodType(models.TextChoices):
        STANDARD = 'standard', 'Standard'
        EXPRESS = 'express', 'Express'
        PICKUP = 'pickup', 'Pickup'
        OTHER = 'other', 'Other'

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="shipping_methods",
    )
    name = models.CharField(max_length=100)
    method_type = models.CharField(max_length=20, choices=MethodType.choices, default=MethodType.STANDARD)
    zones = models.ManyToManyField(
        ShippingZone,
        related_name='shipping_methods',
        blank=True,
        help_text="Zones this method applies to; empty = all zones",
    )
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']
        indexes = [
            models.Index(fields=["store", "is_active", "order"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["store", "name"], name="uniq_shipping_method_store_name"),
        ]

    def __str__(self):
        return self.name


class ShippingRate(models.Model):
    """Pricing rule for a method in a zone (flat, weight-based, or order-total-based)."""

    class RateType(models.TextChoices):
        FLAT = 'flat', 'Flat rate'
        WEIGHT = 'weight', 'Per unit weight'
        ORDER_TOTAL = 'order_total', 'By order total'

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="shipping_rates",
    )
    shipping_method = models.ForeignKey(
        ShippingMethod,
        on_delete=models.CASCADE,
        related_name='rates',
    )
    shipping_zone = models.ForeignKey(
        ShippingZone,
        on_delete=models.CASCADE,
        related_name='rates',
    )
    rate_type = models.CharField(max_length=20, choices=RateType.choices, default=RateType.FLAT)
    min_order_total = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_order_total = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['shipping_method', 'shipping_zone']
        indexes = [
            models.Index(fields=["store", "is_active"]),
            models.Index(fields=["store", "shipping_method", "shipping_zone"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "shipping_method", "shipping_zone", "rate_type", "min_order_total", "max_order_total"],
                name="uniq_shipping_rate_rule",
            ),
        ]

    def __str__(self):
        return f"{self.shipping_method.name} / {self.shipping_zone.name}: {self.price}"
