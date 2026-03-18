from decimal import Decimal

from django.db import models

from engine.apps.stores.models import Store


class StoreAnalytics(models.Model):
    """Daily aggregated store metrics for reporting."""

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name="analytics",
    )
    period_date = models.DateField(db_index=True)
    orders_count = models.PositiveIntegerField(default=0)
    revenue = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    cart_items_count = models.PositiveIntegerField(default=0)
    wishlist_items_count = models.PositiveIntegerField(default=0)
    page_views = models.PositiveIntegerField(default=0, null=True, blank=True)
    conversion_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name_plural = "Store analytics"
        constraints = [
            models.UniqueConstraint(
                fields=["store", "period_date"],
                name="uniq_storeanalytics_store_period",
            )
        ]
        ordering = ["-period_date"]

    def __str__(self):
        return f"{self.store} - {self.period_date}"
