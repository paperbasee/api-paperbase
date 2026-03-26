import json

from channels.generic.websocket import AsyncWebsocketConsumer


class StoreEventsConsumer(AsyncWebsocketConsumer):
    """
    Real-time store events resolved from API key.
    """

    async def connect(self):
        store = self.scope.get("store")
        api_key = self.scope.get("api_key")
        store_pid = self.scope.get("store_public_id")
        if not store or not api_key or not store_pid:
            await self.close(code=4403)
            return
        self.group_name = f"store_{store_pid}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def store_event(self, event):
        await self.send(
            text_data=json.dumps(
                {"event": event.get("event"), "payload": event.get("payload") or {}}
            )
        )
