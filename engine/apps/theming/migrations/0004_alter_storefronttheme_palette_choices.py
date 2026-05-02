from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("theming", "0003_rename_obsidian_to_noir"),
    ]

    operations = [
        migrations.AlterField(
            model_name="storefronttheme",
            name="palette",
            field=models.CharField(
                choices=[
                    ("ivory", "ivory"),
                    ("noir", "noir"),
                    ("arctic", "arctic"),
                    ("sage", "sage"),
                ],
                default="ivory",
                max_length=50,
            ),
        ),
    ]
