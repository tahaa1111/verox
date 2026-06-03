"""
Camera WebSocket relay — Pi pushes frames, browsers receive them in real-time.

Architecture:
  Pi  ──WS──▶  /v1/camera/ws/push/{device_id}  ──relay──▶  /v1/camera/ws/feed/{device_id}  ──WS──▶  Browser

No Redis involved. Pure in-memory fan-out within the single Railway process.

Pi auth:   ?secret=<CAMERA_SECRET>  (query param on WS upgrade)
Browser auth: ?token=<Firebase JWT> (query param on WS upgrade)

Frame message (Pi → relay → browser):
  {"type": "frame", "frame": "<base64>", "stable_progress": 0.0, "job_id": null}

Command message (browser → relay → Pi):
  {"type": "start"} or {"type": "stop"}
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

import structlog
from fastapi import WebSocket

logger = structlog.get_logger(__name__)


class CameraRelayManager:
    def __init__(self) -> None:
        self._pi: dict[str, WebSocket] = {}
        self._viewers: dict[str, set[WebSocket]] = defaultdict(set)
        self._latest_frame: dict[str, str] = {}   # device_id → last base64 JPEG

    # ── Pi connection ──────────────────────────────────────────────────────────

    async def connect_pi(self, device_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._pi[device_id] = ws
        logger.info("camera_pi_connected", device_id=device_id)

    def disconnect_pi(self, device_id: str) -> None:
        self._pi.pop(device_id, None)
        logger.info("camera_pi_disconnected", device_id=device_id)

    # ── Browser connection ─────────────────────────────────────────────────────

    async def connect_viewer(self, device_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self._viewers[device_id].add(ws)
        logger.info("camera_viewer_connected", device_id=device_id,
                    viewers=len(self._viewers[device_id]))

    def disconnect_viewer(self, device_id: str, ws: WebSocket) -> None:
        self._viewers[device_id].discard(ws)
        logger.info("camera_viewer_disconnected", device_id=device_id,
                    viewers=len(self._viewers[device_id]))

    # ── Fan-out: Pi frame → all browsers ──────────────────────────────────────

    async def relay_frame(self, device_id: str, message: dict) -> None:
        if message.get("type") == "frame" and message.get("frame"):
            self._latest_frame[device_id] = message["frame"]
        viewers = list(self._viewers.get(device_id, []))
        if not viewers:
            return
        dead: list[WebSocket] = []
        for ws in viewers:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect_viewer(device_id, ws)

    def get_latest_frame(self, device_id: str) -> str | None:
        return self._latest_frame.get(device_id)

    # ── Fan-in: browser command → Pi ──────────────────────────────────────────

    async def send_command(self, device_id: str, command: str) -> bool:
        pi = self._pi.get(device_id)
        if pi is None:
            return False
        try:
            await pi.send_json({"type": command})
            return True
        except Exception:
            self.disconnect_pi(device_id)
            return False

    def pi_connected(self, device_id: str) -> bool:
        return device_id in self._pi

    def viewer_count(self, device_id: str) -> int:
        return len(self._viewers.get(device_id, set()))


camera_relay = CameraRelayManager()
