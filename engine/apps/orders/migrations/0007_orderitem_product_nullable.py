from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0006_remove_order_delivery_area_alter_order_shipping_zone"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orderitem",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                to="products.product",
            ),
        ),
    ]
