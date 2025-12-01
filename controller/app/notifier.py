from __future__ import annotations

import asyncio
from typing import Set

from fastapi import WebSocket


class ScanNotifier:
    def __init__(self) -> None:
        self.connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self.connections.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self.connections:
                self.connections.remove(websocket)
        try:
            await websocket.close()
        except Exception:
            pass

    async def broadcast(self, message: dict) -> None:
        async with self._lock:
            targets = list(self.connections)
        dead = []
        for connection in targets:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for connection in dead:
            await self.disconnect(connection)


def format_event(event_name: str, payload: dict) -> dict:
    data = {"event": event_name}
    data.update(payload)
    return data


notifier = ScanNotifier()
