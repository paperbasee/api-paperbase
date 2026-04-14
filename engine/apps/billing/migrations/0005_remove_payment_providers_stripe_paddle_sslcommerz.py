# Data migration: remap removed providers; align Payment.provider choices.

from django.db import migrations, models


_DEPRECATED_PROVIDERS = frozenset({"stripe", "paddle", "sslcommerz"})


def forwards_remap_providers(apps, schema_editor):
    Payment = apps.get_model("billing", "Payment")
    Payment.objects.filter(provider__in=_DEPRECATED_PROVIDERS).update(provider="manual")


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0004_alter_subscription_status"),
    ]

    operations = [
        migrations.RunPython(forwards_remap_providers, backwards_noop),
        migrations.AlterField(
            model_name="payment",
            name="provider",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("bkash", "bKash"),
                    ("nagad", "Nagad"),
                ],
                max_length=30,
            ),
        ),
    ]
