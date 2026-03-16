# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="dashboardbranding",
            name="currency_symbol",
            field=models.CharField(blank=True, default="৳", max_length=10),
        ),
    ]
