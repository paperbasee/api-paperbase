from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="total_spent",
            field=models.DecimalField(
                decimal_places=2, default=Decimal("0.00"), max_digits=14
            ),
        ),
        migrations.AddField(
            model_name="customer",
            name="last_order_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RemoveConstraint(
            model_name="customer",
            name="uniq_customer_store_user",
        ),
        migrations.RemoveField(
            model_name="customer",
            name="default_billing_address",
        ),
        migrations.RemoveField(
            model_name="customer",
            name="default_shipping_address",
        ),
        migrations.RemoveField(
            model_name="customer",
            name="marketing_opt_in",
        ),
        migrations.RemoveField(
            model_name="customer",
            name="user",
        ),
        migrations.DeleteModel(
            name="CustomerAddress",
        ),
    ]

