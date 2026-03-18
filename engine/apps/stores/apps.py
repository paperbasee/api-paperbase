from django.apps import AppConfig


class StoresConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "engine.apps.stores"
    verbose_name = "Stores"

    def ready(self):
        import engine.apps.stores.signals  # noqa: F401

