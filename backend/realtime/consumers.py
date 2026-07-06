"""Read-only fan-out WebSocket.

Authenticated clients connect to ``/ws/updates/`` and are placed in one group.
Whenever domain data changes (see ``signals.py``), a small JSON message is
pushed so open pages can refetch. The socket ignores anything the client sends.
"""
import logging
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .broadcast import GROUP

logger = logging.getLogger(__name__)


class UpdatesConsumer(AsyncJsonWebsocketConsumer):

    async def connect(self):
        qs = self.scope["query_string"].decode()
        token_key = qs.split("token=")[-1].split("&")[0] if "token=" in qs else None
        if not token_key:
            await self.close(code=4001)
            return

        user = await self._get_user_from_token(token_key)
        if user is None:
            await self.close(code=4001)
            return

        self._user = user
        await self.channel_layer.group_add(GROUP, self.channel_name)
        await self.accept()
        await self.send_json({"type": "connected"})

    async def disconnect(self, code):
        try:
            await self.channel_layer.group_discard(GROUP, self.channel_name)
        except Exception:
            pass

    async def receive_json(self, content):
        # Clients don't drive anything here — this socket is push-only.
        return

    # channel-layer message with {"type": "broadcast"} → this handler
    async def broadcast(self, event):
        await self.send_json(event["payload"])

    @staticmethod
    async def _get_user_from_token(token_key):
        from django.contrib.auth import get_user_model
        from rest_framework_simplejwt.authentication import JWTAuthentication
        from rest_framework_simplejwt.exceptions import TokenError, InvalidToken

        User = get_user_model()
        try:
            validated = await sync_to_async(JWTAuthentication().get_validated_token)(token_key)
            return await sync_to_async(User.objects.get)(pk=validated["user_id"])
        except (TokenError, InvalidToken, User.DoesNotExist, Exception):
            return None
