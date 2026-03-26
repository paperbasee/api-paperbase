"""
ASGI config: HTTP (Django) + WebSocket (Channels) with API key middleware.
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

from engine.core.rate_limit import WebSocketApiKeyRateLimitMiddleware
from engine.core.routing import websocket_urlpatterns
from engine.core.ws_api_key import APIKeyWebSocketMiddleware

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

django_asgi_app = get_asgi_application()

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
