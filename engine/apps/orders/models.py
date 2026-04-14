import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError

from engine.apps.customers.models import Customer
from engine.core.ids import generate_public_id
from engine.core.tenant_queryset import TenantAwareManager
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
    """Store order. Status: pending (default), confirmed, or cancelled."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        CONFIRMED = 'confirmed', 'Confirmed'
        CANCELLED = 'cancelled', 'Cancelled'

    class Flag(models.TextChoices):
        NO_RESPONSE = "no_response", "No Response"
        CALL_LATER = "call_later", "Call Later"
        WRONG_NUMBER = "wrong_number", "Wrong Number"
        BUSY = "busy", "Busy"
        HIGH_PRIORITY = "high_priority", "High Priority"

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
    flag = models.CharField(
        max_length=32,
        choices=Flag.choices,
        null=True,
        blank=True,
        default=None,
        db_index=True,
    )
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    subtotal_before_discount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Sum of line list extended amounts (original_price × qty).",
    )
    discount_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Sum of per-line discount × qty.",
    )
    subtotal_after_discount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Merchandise total after discounts; equals sum of line_total.",
    )
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
    courier_provider = models.CharField(max_length=20, blank=True, default="")
    courier_consignment_id = models.CharField(max_length=100, blank=True, default="")
    sent_to_courier = models.BooleanField(default=False)
    customer_confirmation_sent_at = models.DateTimeField(
        blank=True,
        null=True,
        help_text="Set when ORDER_CONFIRMED was sent after courier dispatch.",
    )
    pricing_snapshot = models.JSONField(
        blank=True,
        default=dict,
        help_text="JSON audit snapshot of order pricing breakdown (lines + rollups + shipping).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    objects = TenantAwareManager()

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

    @property
    def items_total(self) -> Decimal:
        """
        Product-only order total (excludes shipping). This is the amount used for
        customer `total_spent` rollups.
        """
        return self.subtotal_after_discount


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


class OrderItem(models.Model):
    """Line item with immutable financial snapshot (no live Product reads for totals).

    The variant foreign key is the canonical identity for variant lines; services resolve
    variants by id/public_id, never by SKU (SKU is display-only on serializers).
    """
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
    product_name_snapshot = models.CharField(max_length=255)
    variant_snapshot = models.CharField(max_length=255, null=True, blank=True)
    unit_price_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Final charged unit price at order time.",
    )
    original_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="List/reference unit price frozen at order time.",
    )
    discount_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text="Per unit: original_price − unit_price (may be negative for surcharges).",
    )
    line_subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="original_price × quantity",
    )
    line_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="unit_price × quantity",
    )

    def save(self, *args, **kwargs):
        if self.pk:
            original = OrderItem.objects.get(pk=self.pk)
            if (
                original.product_name_snapshot != self.product_name_snapshot
                or original.variant_snapshot != self.variant_snapshot
                or original.unit_price_snapshot != self.unit_price_snapshot
            ):
                raise ValidationError("Snapshot fields are immutable")
        if not self.public_id:
            self.public_id = generate_public_id("orderitem")
        super().save(*args, **kwargs)

    def __str__(self):
        product_name = self.product.name if self.product else "Unavailable"
        return f"{self.order} - {product_name} x{self.quantity}"


class StockRestoreLog(models.Model):
    class Reason(models.TextChoices):
        CANCELLED = "cancelled", "Cancelled"

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="stock_restore_logs",
    )
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="stock_restore_logs",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="stock_restore_logs",
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    quantity = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["order", "order_item", "reason"],
                name="uniq_order_item_restore_reason",
            )
        ]
        ordering = ["-created_at", "-id"]


class _ImmutableAuditModel(models.Model):
    """Append-only rows: no updates or deletes via the ORM (except raw DB / migrations)."""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Immutable row cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Immutable row cannot be deleted.")


class PurchaseLedgerEntry(_ImmutableAuditModel):
    """
    Immutable per-line purchase record for customer history.
    Survives order and order-item row deletion via denormalized public IDs and SET_NULL FKs.
    """

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. phl_xxx).",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="purchase_ledger_entries",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_ledger_entries",
    )
    customer_public_id = models.CharField(max_length=32, blank=True, default="")
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_ledger_entries",
    )
    order_public_id = models.CharField(max_length=32, db_index=True)
    order_number = models.CharField(max_length=20)
    order_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    order_item = models.ForeignKey(
        OrderItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_ledger_entries",
    )
    order_item_public_id = models.CharField(max_length=32, unique=True, db_index=True)
    product_public_id = models.CharField(max_length=32, blank=True, default="")
    variant_public_id = models.CharField(max_length=32, blank=True, null=True)
    product_name = models.CharField(max_length=255)
    variant_label = models.CharField(max_length=255, blank=True, default="")
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    order_status_snapshot = models.CharField(max_length=20)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        indexes = [
            models.Index(fields=["store", "customer", "recorded_at"]),
            models.Index(fields=["store", "order_public_id"]),
        ]

    objects = TenantAwareManager()

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("purchaseledger")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order_public_id} {self.product_name} x{self.quantity}"


class PurchaseLedgerAdjustment(_ImmutableAuditModel):
    """Append-only correction log; does not modify purchase ledger lines."""

    class FieldKey(models.TextChoices):
        QUANTITY = "quantity", "Quantity"
        UNIT_PRICE = "unit_price", "Unit price"
        VARIANT = "variant", "Variant"
        LINE_REMOVED = "line_removed", "Line removed"
        LINE_ADDED = "line_added", "Line added"

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. pad_xxx).",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="purchase_ledger_adjustments",
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_ledger_adjustments",
    )
    customer_public_id = models.CharField(max_length=32, blank=True, default="")
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_ledger_adjustments",
    )
    order_public_id = models.CharField(max_length=32, blank=True, default="", db_index=True)
    order_item_public_id = models.CharField(max_length=32, blank=True, default="")
    field_key = models.CharField(max_length=32, choices=FieldKey.choices)
    old_value = models.JSONField()
    new_value = models.JSONField()
    reason = models.CharField(max_length=255, default="staff_dashboard_edit")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_ledger_adjustments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["store", "order_public_id", "created_at"]),
            models.Index(fields=["store", "customer", "created_at"]),
        ]

    objects = TenantAwareManager()

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("purchaseadjustment")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.field_key} {self.order_public_id or '—'}"
