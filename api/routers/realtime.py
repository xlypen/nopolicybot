import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/{chat_id}")
async def websocket_endpoint(ws: WebSocket, chat_id: int):
    await ws.accept()
    try:
        while True:
            await asyncio.sleep(30)
            await ws.send_text(json.dumps({"type": "heartbeat", "ts": datetime.utcnow().isoformat(), "chat_id": chat_id}))
    except WebSocketDisconnect:
        return
