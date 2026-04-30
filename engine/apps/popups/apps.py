from django.apps import AppConfig


class PopupsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "engine.apps.popups"
    label = "popups"
    verbose_name = "Popups"

    def ready(self):
        import engine.apps.popups.signals  # noqa: F401

