from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce

from engine.apps.customers.models import Customer
from engine.apps.orders.models import Order


class Command(BaseCommand):
    help = (
        "One-time correction: rebuild customers.total_spent from CONFIRMED orders only, "
        "excluding shipping (uses Order.subtotal_after_discount)."
    )

    def handle(self, *args, **options):
        updated = 0
        with transaction.atomic():
            for c in Customer.objects.select_for_update().all().iterator(chunk_size=500):
                agg = (
                    Order.objects.filter(
                        store_id=c.store_id,
                        customer_id=c.pk,
                        status=Order.Status.CONFIRMED,
                    ).aggregate(
                        spent=Coalesce(Sum("subtotal_after_discount"), Decimal("0.00"))
                    )
                )
                spent = agg["spent"] or Decimal("0.00")
                if c.total_spent != spent:
                    c.total_spent = spent
                    c.save(update_fields=["total_spent", "updated_at"])
                    updated += 1

        self.stdout.write(self.style.SUCCESS(f"Updated {updated} customers."))

