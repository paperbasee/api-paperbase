from django.apps import AppConfig


class SupportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "engine.apps.support"
    label = "support"
    verbose_name = "Support"

    def ready(self):
        import engine.apps.support.signals  # noqa: F401
