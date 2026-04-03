from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0003_orderitem_snapshots_backfill"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orderitem",
            name="product_name_snapshot",
            field=models.CharField(max_length=255),
        ),
        migrations.AlterField(
            model_name="orderitem",
            name="unit_price_snapshot",
            field=models.DecimalField(decimal_places=2, max_digits=12),
        ),
    ]
