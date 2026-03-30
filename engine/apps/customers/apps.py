from django.apps import AppConfig


class CustomersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'engine.apps.customers'
    label = 'customers'
    verbose_name = 'Customers'

    def ready(self):
        import engine.apps.customers.signals  # noqa: F401
