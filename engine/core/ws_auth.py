"""JWT + active store claim for dashboard WebSocket connections."""

from __future__ import annotations

from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken


def _user_by_public_id(User, uid: str):
    return User.objects.filter(public_id=uid, is_active=True).first()


class JWTStoreWebSocketMiddleware:
    """
    Parse `?token=<access_jwt>` and populate scope[\"user\"] and scope[\"ws_active_store_public_id\"].
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            return await self.inner(scope, receive, send)
        scope["ws_active_store_public_id"] = None
        scope["user"] = AnonymousUser()
        qs = parse_qs(scope.get("query_string", b"").decode("utf-8"))
        token = (qs.get("token") or [None])[0]
        if not token:
            return await self.inner(scope, receive, send)
        try:
            access = AccessToken(token)
            claim = settings.SIMPLE_JWT["USER_ID_CLAIM"]
            uid = access.get(claim)
            active_store_public_id = access.get("active_store_public_id")
            scope["ws_active_store_public_id"] = str(active_store_public_id) if active_store_public_id is not None else None
            if uid:
                from django.contrib.auth import get_user_model

                User = get_user_model()
                user = await sync_to_async(_user_by_public_id)(User, uid)
                if user:
                    scope["user"] = user
        except (TokenError, InvalidToken, KeyError):
            pass
        return await self.inner(scope, receive, send)
