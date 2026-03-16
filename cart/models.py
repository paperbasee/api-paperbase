from django.conf import settings
from django.db import models

from products.models import Product


class Cart(models.Model):
    """Cart: for authenticated user or anonymous (session_key)."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
        related_name='carts'
    )
    session_key = models.CharField(max_length=40, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"Cart {self.pk} ({self.user or self.session_key or 'anon'})"


class CartItem(models.Model):
    """Cart line item: product, quantity, optional size."""
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    size = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [['cart', 'product', 'size']]
        ordering = ['created_at']

    def __str__(self):
        return f"{self.cart} - {self.product.name} x{self.quantity}"
