from django.conf import settings
from django.db import models

from products.models import Product


class WishlistItem(models.Model):
    """Wishlist entry: for authenticated user or anonymous (session_key)."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='wishlist_items',
    )
    session_key = models.CharField(max_length=40, blank=True, db_index=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='wishlist_items')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'product'],
                condition=models.Q(user__isnull=False),
                name='unique_user_wishlist_item',
            ),
            models.UniqueConstraint(
                fields=['session_key', 'product'],
                condition=models.Q(user__isnull=True),
                name='unique_session_wishlist_item',
            ),
        ]
        ordering = ['-created_at']

    def __str__(self):
        owner = self.user or self.session_key or 'anon'
        return f"{owner} - {self.product.name}"
