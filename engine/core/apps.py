from django.apps import AppConfig
from django.conf import settings


def enforce_production_override_safety() -> None:
    is_production = not bool(getattr(settings, "DEBUG", False)) and not bool(
        getattr(settings, "TESTING", False)
    )
    if not is_production:
        return
    if bool(getattr(settings, "SECURITY_INTERNAL_OVERRIDE_ALLOWED", False)):
        raise RuntimeError(
            "SECURITY_INTERNAL_OVERRIDE_ALLOWED must remain false in production."
        )


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "engine.core"
    label = "core"
    verbose_name = "Core"

    def ready(self):
        from engine.core.store_api_key_auth import maybe_validate_storefront_api_key_view_flags
        from engine.core.safety.tenant_safety import register_tenant_safety_hooks

        enforce_production_override_safety()
        register_tenant_safety_hooks()
        maybe_validate_storefront_api_key_view_flags()
