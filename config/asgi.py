import os

import newrelic.agent

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.runtime")
newrelic.agent.initialize()

from django.core.asgi import get_asgi_application

application = newrelic.agent.ASGIApplicationWrapper(get_asgi_application())
