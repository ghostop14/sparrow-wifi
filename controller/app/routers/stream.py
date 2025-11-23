from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..notifier import notifier

router = APIRouter()


@router.websocket("/ws/scans")
async def scans_stream(websocket: WebSocket):
    await notifier.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await notifier.disconnect(websocket)
    except Exception:
        await notifier.disconnect(websocket)
