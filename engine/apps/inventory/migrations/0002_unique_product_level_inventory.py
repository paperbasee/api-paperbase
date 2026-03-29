from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="inventory",
            constraint=models.UniqueConstraint(
                condition=models.Q(("variant__isnull", True)),
                fields=("product",),
                name="unique_product_level_inventory",
            ),
        ),
    ]
