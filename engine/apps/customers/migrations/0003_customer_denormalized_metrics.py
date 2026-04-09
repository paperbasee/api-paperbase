from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0002_customer_minimal_schema"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="first_order_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="customer",
            name="is_repeat_customer",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="customer",
            name="avg_order_interval_days",
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=10, null=True
            ),
        ),
    ]

