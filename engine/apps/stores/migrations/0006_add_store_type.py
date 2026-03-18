# Generated manually - add store_type field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0005_remove_store_description"),
    ]

    operations = [
        migrations.AddField(
            model_name="store",
            name="store_type",
            field=models.CharField(
                blank=True,
                help_text="Store type/category (e.g. Fashion, Retail, E-commerce). Max 4 words.",
                max_length=60,
            ),
        ),
    ]
