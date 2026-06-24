"""
ws_manager.py — WebSocket connection manager (standalone module).

Extracted from main.py so mqtt_subscriber.py can import it
without creating a circular dependency.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: Dict[str, WebSocket] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at FastAPI startup to capture the running event loop."""
        self._loop = loop

    async def connect(self, client_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[client_id] = websocket

    def disconnect(self, client_id: str) -> None:
        self._connections.pop(client_id, None)

    def count(self) -> int:
        """目前連線中的 WebSocket 數量。"""
        return len(self._connections)

    async def broadcast(self, msg: dict) -> None:
        dead: list[str] = []
        for client_id, ws in list(self._connections.items()):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(client_id)
        for client_id in dead:
            self.disconnect(client_id)

    def broadcast_from_thread(self, msg: dict) -> None:
        """Thread-safe broadcast — call from MQTT subscriber thread."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(msg), self._loop)
        else:
            logger.debug("No event loop available; skipping WS broadcast")


ws_manager = ConnectionManager()
