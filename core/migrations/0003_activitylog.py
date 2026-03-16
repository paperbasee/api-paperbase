# Generated manually for ActivityLog

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_dashboardbranding_currency_symbol"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ActivityLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("create", "Create"), ("update", "Update"), ("delete", "Delete"), ("custom", "Custom")], max_length=20)),
                ("entity_type", models.CharField(max_length=50)),
                ("entity_id", models.CharField(blank=True, default="", max_length=64)),
                ("summary", models.CharField(max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="admin_activity_logs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="activitylog",
            index=models.Index(fields=["-created_at"], name="core_activi_created_7d4c0f_idx"),
        ),
        migrations.AddIndex(
            model_name="activitylog",
            index=models.Index(fields=["entity_type", "action", "-created_at"], name="core_activi_entity__a9d251_idx"),
        ),
    ]

