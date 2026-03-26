from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("stores", "0004_storesettings_public_api_enabled"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="storeapikey",
            name="one_active_store_api_key",
        ),
    ]
