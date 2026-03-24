import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth.models import AnonymousUser


class StoreEventsConsumer(AsyncWebsocketConsumer):
    """
    Real-time store events. Requires:
    - Host resolves to a verified Domain (scope[\"store_public_id\"])
    - JWT active_store_public_id matches that store
    - Authenticated user
    """

    async def connect(self):
        store_pid = self.scope.get("store_public_id")
        token_store = self.scope.get("ws_active_store_public_id")
        user = self.scope.get("user")
        # Same rule as DRF PermissionDenied: JWT active_store_public_id must match Host-resolved store.
        if (
            not store_pid
            or not token_store
            or token_store != store_pid
            or not user
            or isinstance(user, AnonymousUser)
        ):
            await self.close(code=4403)
            return
        member_ok = await database_sync_to_async(self._has_active_membership)(user, store_pid)
        if not member_ok:
            await self.close(code=4403)
            return
        self.group_name = f"store_{store_pid}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    def _has_active_membership(self, user, store_public_id: str) -> bool:
        from engine.apps.stores.models import Store, StoreMembership

        store = Store.objects.filter(public_id=store_public_id, is_active=True).first()
        if not store:
            return False
        return StoreMembership.objects.filter(
            user=user,
            store=store,
            is_active=True,
        ).exists()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def store_event(self, event):
        await self.send(
            text_data=json.dumps(
                {"event": event.get("event"), "payload": event.get("payload") or {}}
            )
        )
