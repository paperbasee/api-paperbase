from django.apps import AppConfig


class BackupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "engine.apps.backup"
    label = "backup"
    verbose_name = "Backup"
