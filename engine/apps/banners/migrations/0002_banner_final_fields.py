from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("banners", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="banner",
            old_name="link_url",
            new_name="redirect_url",
        ),
        migrations.RenameField(
            model_name="banner",
            old_name="position",
            new_name="placement",
        ),
        migrations.RenameField(
            model_name="banner",
            old_name="order",
            new_name="position",
        ),
        migrations.AlterField(
            model_name="banner",
            name="placement",
            field=models.CharField(
                choices=[
                    ("homepage", "Homepage"),
                    ("sidebar", "Sidebar"),
                    ("footer", "Footer"),
                    ("header", "Header"),
                ],
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="banner",
            name="cta_text",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="banner",
            name="description",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="banner",
            name="is_clickable",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterModelOptions(
            name="banner",
            options={"ordering": ["placement", "position", "id"]},
        ),
    ]
