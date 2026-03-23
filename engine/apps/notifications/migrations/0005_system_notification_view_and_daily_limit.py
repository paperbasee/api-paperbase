import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("notifications", "0004_notification_store"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemnotification",
            name="daily_limit",
            field=models.IntegerField(
                default=3,
                help_text="How many times a user must dismiss before hiding for the day",
            ),
        ),
        migrations.CreateModel(
            name="SystemNotificationView",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "public_id",
                    models.CharField(
                        db_index=True,
                        editable=False,
                        help_text="Non-sequential public identifier (e.g. ntv_xxx).",
                        max_length=32,
                        unique=True,
                    ),
                ),
                ("date", models.DateField()),
                ("dismiss_count", models.IntegerField(default=0)),
                (
                    "notification",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="view_records",
                        to="notifications.systemnotification",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="system_notification_views",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-date", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="systemnotificationview",
            constraint=models.UniqueConstraint(
                fields=("user", "notification", "date"),
                name="notifications_sysnotifview_user_notif_date_uniq",
            ),
        ),
    ]
