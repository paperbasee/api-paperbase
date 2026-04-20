"""
Legacy entrypoint: prefer DJANGO_SETTINGS_MODULE=config.settings.runtime with DEBUG=false.

Preserves the old guard: DEBUG must not be enabled when loading this module.
"""
import os

from django.core.exceptions import ImproperlyConfigured


def _truthy_debug() -> bool:
    v = os.getenv("DEBUG")
    if v is None:
        return False
    return v.strip().lower() in {"1", "true", "yes", "on"}


if _truthy_debug():
    raise ImproperlyConfigured("DEBUG must be False when using config.settings.production.")

os.environ["DEBUG"] = "false"

from .runtime import *  # noqa: E402,F403,F401
