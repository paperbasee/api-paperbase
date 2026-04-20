"""
ASGI config: HTTP (Django) + WebSocket (Channels) with API key middleware.

Import order: initialize Django (get_asgi_application) before any project imports
that touch the ORM, or AppRegistryNotReady will occur under Gunicorn workers.
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.runtime")

from django.core.asgi import get_asgi_application

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

from engine.core.rate_limit import WebSocketApiKeyRateLimitMiddleware
from engine.core.routing import websocket_urlpatterns
from engine.core.ws_api_key import APIKeyWebSocketMiddleware

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            APIKeyWebSocketMiddleware(
                WebSocketApiKeyRateLimitMiddleware(
                    URLRouter(websocket_urlpatterns),
                )
            )
        ),
    }
)
