from decimal import Decimal

from django.core.management.base import BaseCommand

from engine.apps.billing.models import Plan


PLAN_SEED_DATA = (
    {
        "name": "Essential",
        "price": Decimal("650.00"),
        "billing_cycle": Plan.BillingCycle.MONTHLY,
        "features": {
            "limits": {"max_stores": 1, "max_products": 100},
            "features": {
                "advanced_analytics": False,
                "order_email_notifications": False,
            },
        },
    },
    {
        "name": "Premium",
        "price": Decimal("950.00"),
        "billing_cycle": Plan.BillingCycle.MONTHLY,
        "features": {
            "limits": {"max_stores": 3, "max_products": 200},
            "features": {
                "advanced_analytics": True,
                "order_email_notifications": True,
            },
        },
    },
)


class Command(BaseCommand):
    help = "Create or update default plan rows for Essential and Premium."

    def handle(self, *args, **options):
        created = 0
        updated = 0
        skipped = 0

        for payload in PLAN_SEED_DATA:
            name = payload["name"]
            defaults = {
                "price": payload["price"],
                "billing_cycle": payload["billing_cycle"],
                "features": payload["features"],
                "is_active": True,
            }
            obj, was_created = Plan.objects.get_or_create(
                name=name,
                defaults=defaults,
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created plan: {name}"))
                continue

            update_fields = []
            for field, value in defaults.items():
                if getattr(obj, field) != value:
                    setattr(obj, field, value)
                    update_fields.append(field)

            if not update_fields:
                skipped += 1
                self.stdout.write(f"Skipped existing plan: {name}")
                continue

            obj.save(update_fields=[*update_fields, "updated_at"])
            updated += 1
            self.stdout.write(self.style.WARNING(f"Updated plan: {name}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created={created}, Updated={updated}, Skipped={skipped}"
            )
        )
