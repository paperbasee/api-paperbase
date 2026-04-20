"""
Legacy entrypoint: prefer DJANGO_SETTINGS_MODULE=config.settings.runtime with DEBUG=true.

Forces DEBUG on (matches former hard-coded development profile; keeps pytest deterministic).
"""
import os

os.environ["DEBUG"] = "true"

from .runtime import *  # noqa: E402,F403,F401
