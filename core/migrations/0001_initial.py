# Generated manually for DashboardBranding

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="DashboardBranding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("logo", models.ImageField(blank=True, null=True, upload_to="branding/")),
                ("admin_name", models.CharField(default="Gadzilla", max_length=100)),
                ("admin_subtitle", models.CharField(default="Admin dashboard", max_length=200)),
            ],
            options={
                "verbose_name": "Dashboard branding",
                "verbose_name_plural": "Dashboard branding",
            },
        ),
    ]
