from django.conf import settings
from django.db import models

from engine.core.ids import generate_public_id


class Review(models.Model):
    """Product review with star rating and optional moderation."""

    public_id = models.CharField(
        max_length=32, unique=True, db_index=True, editable=False,
        help_text="Non-sequential public identifier (e.g. rev_xxx).",
    )

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    product = models.ForeignKey(
        'products.Product',
        on_delete=models.CASCADE,
        related_name='reviews',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='product_reviews',
    )
    rating = models.PositiveSmallIntegerField(
        help_text="1-5 stars",
    )
    title = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [['product', 'user']]

    def save(self, *args, **kwargs):
        if not self.public_id:
            self.public_id = generate_public_id("review")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Review {self.product.name} by {self.user_id} - {self.rating}"
