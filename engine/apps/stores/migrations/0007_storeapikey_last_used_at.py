from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stores", "0006_remove_domain_store_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="storeapikey",
            name="last_used_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
