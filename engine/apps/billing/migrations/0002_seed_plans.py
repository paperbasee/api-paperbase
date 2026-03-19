"""
Seed the two core subscription plans: Basic and Premium.

This migration is the single source of truth for plan definitions.
To update plan features in production, add a new migration — never edit this one.
"""

from django.db import migrations


def seed_plans(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    import uuid

    plans = [
        {
            "name": "basic",
            "price": "299.00",
            "billing_cycle": "monthly",
            "is_default": False,
            "is_active": True,
            "public_id": f"pln_{uuid.uuid4().hex[:20]}",
            "features": {
                "limits": {
                    "max_stores": 1,
                    "max_products": 200,
                    "max_staff_members": 2,
                },
                "features": {
                    "carts": True,
                    "wishlist": True,
                    "categories": True,
                    "coupons": True,
                    "reviews": True,
                    "banners": True,
                    "shipping": True,
                    "inventory": True,
                    "analytics": False,
                    "advanced_analytics": False,
                    "custom_domain": False,
                    "priority_support": False,
                },
            },
        },
        {
            "name": "premium",
            "price": "799.00",
            "billing_cycle": "monthly",
            "is_default": False,
            "is_active": True,
            "public_id": f"pln_{uuid.uuid4().hex[:20]}",
            "features": {
                "limits": {
                    "max_stores": 5,
                    "max_products": -1,   # -1 = unlimited
                    "max_staff_members": -1,
                },
                "features": {
                    "carts": True,
                    "wishlist": True,
                    "categories": True,
                    "coupons": True,
                    "reviews": True,
                    "banners": True,
                    "shipping": True,
                    "inventory": True,
                    "analytics": True,
                    "advanced_analytics": True,
                    "custom_domain": True,
                    "priority_support": True,
                },
            },
        },
    ]

    for plan_data in plans:
        Plan.objects.get_or_create(name=plan_data["name"], defaults=plan_data)


def remove_plans(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    Plan.objects.filter(name__in=["basic", "premium"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_plans, reverse_code=remove_plans),
    ]
