from django.conf import settings
from django.db import models

from engine.apps.products.models import Product
from engine.core.ids import generate_public_id


class Cart(models.Model):
    """Cart: for authenticated user or anonymous (session_key)."""
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. crt_xxx).",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
        related_name='carts'
    )
    session_key = models.CharField(max_length=40, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("cart")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Cart {self.pk} ({self.user or self.session_key or 'anon'})"


class CartItem(models.Model):
    """Cart line item: product, optional variant, quantity, optional size."""
    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. cit_xxx).",
    )
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    variant = models.ForeignKey(
        'products.ProductVariant',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='cart_items',
    )
    quantity = models.PositiveIntegerField(default=1)
    size = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [['cart', 'product', 'size']]
        ordering = ['created_at']

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("cartitem")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cart} - {self.product.name} x{self.quantity}"
