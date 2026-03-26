from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stores", "0003_rename_stores_stor_store_i_2527eb_idx_stores_stor_store_i_d05ae1_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="storesettings",
            name="public_api_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Allow public storefront read endpoints without API key for this store.",
            ),
        ),
    ]
