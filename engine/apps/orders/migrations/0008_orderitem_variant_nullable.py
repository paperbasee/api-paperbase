from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0007_orderitem_product_nullable"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orderitem",
            name="variant",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="order_items",
                to="products.productvariant",
            ),
        ),
    ]
