from django.apps import AppConfig


class BannersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "engine.apps.banners"
    label = "banners"
    verbose_name = "Banners"
