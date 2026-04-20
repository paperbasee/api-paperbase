import os

from celery import Celery


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.runtime")

app = Celery("config")

# Pull Celery settings from Django settings (prefixed with `CELERY_`).
app.config_from_object("django.conf:settings", namespace="CELERY")

# Discover tasks from installed apps.
app.autodiscover_tasks()

