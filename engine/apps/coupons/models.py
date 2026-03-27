from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError

from engine.apps.products.models import Category, Product
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
    per_user_max_uses = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Max successful usages per authenticated user within this store.",
    )
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


class CouponUsage(models.Model):
    """Per-order coupon usage audit row for enforcement and analytics."""

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. cpu_xxx).",
    )
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="coupon_usages",
    )
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.CASCADE,
        related_name="usages",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="coupon_usages",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coupon_usages",
    )
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    is_reversed = models.BooleanField(default=False)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reverse_reason = models.CharField(max_length=20, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "coupon", "order"],
                name="uniq_coupon_usage_store_coupon_order",
            )
        ]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("couponusage")
        super().save(*args, **kwargs)


class BulkDiscount(models.Model):
    """Store-scoped automatic discount rules for catalog entities."""

    public_id = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        editable=False,
        help_text="Non-sequential public identifier (e.g. bdk_xxx).",
    )

    class TargetType(models.TextChoices):
        CATEGORY = "category", "Category"
        SUBCATEGORY = "subcategory", "Subcategory"
        PRODUCT = "product", "Product"

    class DiscountType(models.TextChoices):
        PERCENTAGE = "percentage", "Percentage"
        FIXED = "fixed", "Fixed amount"

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="bulk_discounts",
    )
    target_type = models.CharField(
        max_length=20,
        choices=TargetType.choices,
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="bulk_discounts",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="bulk_discounts",
    )
    discount_type = models.CharField(
        max_length=20,
        choices=DiscountType.choices,
    )
    discount_value = models.DecimalField(max_digits=10, decimal_places=2)
    priority = models.PositiveIntegerField(default=0)
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-priority", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["store", "target_type", "category", "product", "priority"],
                name="uniq_bulk_discount_store_target_priority",
            )
        ]

    def clean(self):
        if self.target_type == self.TargetType.PRODUCT:
            if not self.product_id or self.category_id:
                raise ValidationError("Product discount must set product only.")
        elif self.target_type == self.TargetType.CATEGORY:
            if not self.category_id or self.product_id:
                raise ValidationError("Category discount must set category only.")
            if self.category and self.category.parent_id is not None:
                raise ValidationError("Category target must be a top-level category.")
        elif self.target_type == self.TargetType.SUBCATEGORY:
            if not self.category_id or self.product_id:
                raise ValidationError("Subcategory discount must set category only.")
            if self.category and self.category.parent_id is None:
                raise ValidationError("Subcategory target must be a child category.")

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("bulkdiscount")
        self.full_clean()
        super().save(*args, **kwargs)
