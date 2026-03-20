from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("banners", "0002_banner_final_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="banner",
            name="placement",
            field=models.CharField(db_index=True, max_length=50),
        ),
    ]
