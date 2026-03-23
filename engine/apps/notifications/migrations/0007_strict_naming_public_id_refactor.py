from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0006_alter_systemnotificationview_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameModel(
            old_name="Notification",
            new_name="StorefrontCTA",
        ),
        migrations.RenameModel(
            old_name="SystemNotification",
            new_name="PlatformNotification",
        ),
        migrations.RenameModel(
            old_name="StaffInboxNotification",
            new_name="StaffNotification",
        ),
        migrations.RenameModel(
            old_name="SystemNotificationView",
            new_name="NotificationDismissal",
        ),
        migrations.RenameField(
            model_name="storefrontcta",
            old_name="text",
            new_name="cta_text",
        ),
        migrations.AlterField(
            model_name="platformnotification",
            name="public_id",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text="Non-sequential public identifier (e.g. sys_xxx).",
                max_length=32,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="staffnotification",
            name="user",
            field=models.ForeignKey(
                blank=True,
                help_text="Null = visible to all staff",
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="staff_notifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="storefrontcta",
            name="public_id",
            field=models.CharField(
                db_index=True,
                editable=False,
                help_text="Non-sequential public identifier (e.g. cta_xxx).",
                max_length=32,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="storefrontcta",
            name="store",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="storefront_ctas",
                to="stores.store",
            ),
        ),
        migrations.AlterField(
            model_name="notificationdismissal",
            name="notification",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="notification_dismissals",
                to="notifications.platformnotification",
            ),
        ),
        migrations.AlterField(
            model_name="notificationdismissal",
            name="user",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="platform_notifications",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="notificationdismissal",
            name="notifications_sysnotifview_user_notif_date_uniq",
        ),
        migrations.AddConstraint(
            model_name="notificationdismissal",
            constraint=models.UniqueConstraint(
                fields=("user", "notification", "date"),
                name="notifications_notifdismiss_user_notif_date_uniq",
            ),
        ),
    ]
