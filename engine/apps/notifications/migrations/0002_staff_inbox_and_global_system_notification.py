# Generated manually for StaffInboxNotification rename + SystemNotification (global banner)

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="SystemNotification",
            new_name="StaffInboxNotification",
        ),
        migrations.AlterField(
            model_name="staffinboxnotification",
            name="user",
            field=models.ForeignKey(
                blank=True,
                help_text="Null = visible to all staff",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="staff_inbox_notifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="SystemNotification",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "public_id",
                    models.CharField(
                        db_index=True,
                        editable=False,
                        help_text="Non-sequential public identifier (e.g. ntf_xxx).",
                        max_length=32,
                        unique=True,
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("message", models.TextField()),
                (
                    "cta_text",
                    models.CharField(blank=True, max_length=100, null=True),
                ),
                ("cta_url", models.URLField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("start_at", models.DateTimeField()),
                ("end_at", models.DateTimeField(blank=True, null=True)),
                (
                    "priority",
                    models.IntegerField(
                        default=0,
                        help_text="Higher values win when multiple notifications are active.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-priority", "-created_at"],
            },
        ),
    ]
