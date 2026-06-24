"""
ws_manager.py — WebSocket connection manager (standalone module).

Extracted from main.py so mqtt_subscriber.py can import it
without creating a circular dependency.

每條連線都記錄 metadata（IP、連線時間），供後台「線上連線清單 / 踢人」使用。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # client_id -> {"ws": WebSocket, "ip": str, "connected_at": float}
        self._connections: Dict[str, dict] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at FastAPI startup to capture the running event loop."""
        self._loop = loop

    async def connect(self, client_id: str, websocket: WebSocket, ip: str = "") -> None:
        await websocket.accept()
        self._connections[client_id] = {
            "ws": websocket,
            "ip": ip,
            "connected_at": time.time(),
        }

    def disconnect(self, client_id: str) -> None:
        self._connections.pop(client_id, None)

    def count(self) -> int:
        """目前連線中的 WebSocket 數量。"""
        return len(self._connections)

    def list_clients(self) -> list[dict]:
        """回傳所有連線的資訊（給後台顯示）。"""
        now = time.time()
        return [
            {
                "client_id": cid,
                "ip": entry.get("ip", ""),
                "connected_seconds": int(now - entry.get("connected_at", now)),
            }
            for cid, entry in self._connections.items()
        ]

    async def kick(self, client_id: str) -> bool:
        """強制中斷指定連線。回傳是否成功找到並踢除。"""
        entry = self._connections.get(client_id)
        if not entry:
            return False
        try:
            await entry["ws"].close(code=4001)  # 4001 = 自訂「被管理員踢除」
        except Exception:
            pass
        self.disconnect(client_id)
        return True

    async def broadcast(self, msg: dict) -> None:
        dead: list[str] = []
        for client_id, entry in list(self._connections.items()):
            try:
                await entry["ws"].send_json(msg)
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
