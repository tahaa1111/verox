"""
WebSocket connection manager with Redis pub/sub fan-out.
Works across multiple Cloud Run instances — each subscribes to ws:job:*.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict

import redis.asyncio as aioredis
import structlog
from fastapi import WebSocket

from services.api.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

HEARTBEAT_INTERVAL = 15  # seconds (spec §3.3)
WS_CHANNEL_PREFIX = "ws:job:"


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._redis: aioredis.Redis | None = None
        self._pubsub_task: asyncio.Task | None = None

    @property
    def active_count(self) -> int:
        return sum(len(ws) for ws in self._connections.values())

    async def startup(self) -> None:
        redis_kwargs: dict = {"decode_responses": True}
        auth = settings.redis_auth_string
        if auth:
            redis_kwargs["password"] = auth
        self._redis = aioredis.from_url(settings.redis_url, **redis_kwargs)
        self._pubsub_task = asyncio.create_task(self._pubsub_listener())
        logger.info("ws_manager_started")

    async def shutdown(self) -> None:
        if self._pubsub_task:
            self._pubsub_task.cancel()
        if self._redis:
            await self._redis.aclose()

    async def connect(self, job_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[job_id].add(websocket)
        logger.info("ws_connect", job_id=job_id)

    def disconnect(self, job_id: str, websocket: WebSocket) -> None:
        self._connections[job_id].discard(websocket)
        if not self._connections[job_id]:
            del self._connections[job_id]
        logger.info("ws_disconnect", job_id=job_id)

    async def send_to_job(self, job_id: str, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._connections.get(job_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(job_id, ws)

    async def _pubsub_listener(self) -> None:
        """Subscribe to ws:job:* and fan messages out to connected WebSockets."""
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe(f"{WS_CHANNEL_PREFIX}*")
        logger.info("ws_pubsub_subscribed", pattern=f"{WS_CHANNEL_PREFIX}*")
        try:
            async for message in pubsub.listen():
                if message["type"] != "pmessage":
                    continue
                channel: str = message["channel"]
                job_id = channel.removeprefix(WS_CHANNEL_PREFIX)
                try:
                    data = json.loads(message["data"])
                    await self.send_to_job(job_id, data)
                except Exception as exc:
                    logger.error("ws_pubsub_dispatch_error", job_id=job_id, exc=str(exc))
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.punsubscribe()
            await pubsub.aclose()


ws_manager = ConnectionManager()
