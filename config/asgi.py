import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.runtime")
django.setup()

import newrelic.agent

newrelic.agent.initialize()

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator

from engine.core.rate_limit import WebSocketApiKeyRateLimitMiddleware
from engine.core.routing import websocket_urlpatterns
from engine.core.ws_api_key import APIKeyWebSocketMiddleware


django_asgi_app = newrelic.agent.ASGIApplicationWrapper(get_asgi_application())


async def lifespan_app(scope, receive, send):
    while True:
        message = await receive()
        message_type = message.get("type")
        if message_type == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message_type == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


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
        "lifespan": lifespan_app,
    }
)
