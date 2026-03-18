# Data migration: seed Basic and Premium plans

from django.db import migrations


def seed_plans(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.get_or_create(
        name="basic",
        defaults={
            "price": 0,
            "billing_cycle": "monthly",
            "max_stores": 1,
            "features": {"advanced_analytics": False, "priority_support": False},
            "is_active": True,
        },
    )
    Plan.objects.get_or_create(
        name="premium",
        defaults={
            "price": 999,
            "billing_cycle": "monthly",
            "max_stores": 3,
            "features": {"advanced_analytics": True, "priority_support": False},
            "is_active": True,
        },
    )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_plans, noop),
    ]
