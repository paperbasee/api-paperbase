from decimal import Decimal

from django.core.management.base import BaseCommand

from engine.apps.billing.models import Plan


PLAN_SEED_DATA = (
    {
        "name": "Essential",
        "price": Decimal("750.00"),
        "billing_cycle": Plan.BillingCycle.MONTHLY,
        "features": {
            "limits": {
                "max_products": 100,
                "storefront_requests_per_minute": 100,
            },
            "features": {
                "basic_analytics": True,
                "order_email_notifications": False,
            },
        },
    },
    {
        "name": "Essential",
        "price": Decimal("650.00"),
        "billing_cycle": Plan.BillingCycle.YEARLY,
        "features": {
            "limits": {
                "max_products": 100,
                "storefront_requests_per_minute": 100,
            },
            "features": {
                "basic_analytics": True,
                "order_email_notifications": False,
            },
        },
    },
    {
        "name": "Premium",
        "price": Decimal("950.00"),
        "billing_cycle": Plan.BillingCycle.MONTHLY,
        "features": {
            "limits": {
                "max_products": 500,
                "storefront_requests_per_minute": 500,
            },
            "features": {
                "basic_analytics": True,
                "order_email_notifications": True,
            },
        },
    },
    {
        "name": "Premium",
        "price": Decimal("800.00"),
        "billing_cycle": Plan.BillingCycle.YEARLY,
        "features": {
            "limits": {
                "max_products": 500,
                "storefront_requests_per_minute": 500,
            },
            "features": {
                "basic_analytics": True,
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
            billing_cycle = payload["billing_cycle"]
            defaults = {
                "price": payload["price"],
                "billing_cycle": billing_cycle,
                "features": payload["features"],
                "is_active": True,
            }
            obj, was_created = Plan.objects.get_or_create(
                name=name,
                billing_cycle=billing_cycle,
                defaults=defaults,
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f"Created plan: {name} ({billing_cycle})"))
                continue

            update_fields = []
            for field, value in defaults.items():
                if getattr(obj, field) != value:
                    setattr(obj, field, value)
                    update_fields.append(field)

            if not update_fields:
                skipped += 1
                self.stdout.write(f"Skipped existing plan: {name} ({billing_cycle})")
                continue

            obj.save(update_fields=[*update_fields, "updated_at"])
            updated += 1
            self.stdout.write(self.style.WARNING(f"Updated plan: {name} ({billing_cycle})"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created={created}, Updated={updated}, Skipped={skipped}"
            )
        )
