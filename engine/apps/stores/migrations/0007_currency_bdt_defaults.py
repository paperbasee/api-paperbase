# Generated manually - default currency to BDT and symbol to ৳ (Taka)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0006_add_store_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="store",
            name="currency",
            field=models.CharField(default="BDT", max_length=8),
        ),
        migrations.AlterField(
            model_name="store",
            name="currency_symbol",
            field=models.CharField(blank=True, default="৳", max_length=10),
        ),
    ]
