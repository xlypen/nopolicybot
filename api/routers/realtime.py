from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

import bot_settings
import social_graph
from api.dependencies import require_auth
from services.realtime_broadcast import BroadcastManager

router = APIRouter()
logger = logging.getLogger(__name__)

_broadcast_manager = BroadcastManager(queue_size=64, heartbeat_sec=25)
_broadcast_task: asyncio.Task | None = None


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _parse_realtime_result(raw) -> tuple[int, dict[int, int]]:
    if isinstance(raw, dict):
        updated = int(raw.get("updated", 0) or 0)
        by_chat = {}
        for chat_id, count in (raw.get("by_chat") or {}).items():
            try:
                by_chat[int(chat_id)] = int(count or 0)
            except Exception:
                continue
        return updated, by_chat
    return int(raw or 0), {}


async def _realtime_broadcast_loop() -> None:
    loop = asyncio.get_running_loop()
    logger.info("Realtime broadcast loop started")
    while True:
        try:
            enabled = bool(bot_settings.get("social_graph_realtime_enabled"))
            interval = bot_settings.get_int("social_graph_realtime_interval_sec", lo=15, hi=1800)
            min_new = bot_settings.get_int("social_graph_realtime_min_new_messages", lo=1, hi=20)
            if enabled:
                raw = await loop.run_in_executor(
                    None,
                    lambda: social_graph.process_realtime_updates(min_new_messages=min_new, return_details=True),
                )
                updated, by_chat = _parse_realtime_result(raw)
                if updated > 0:
                    active_chats = await _broadcast_manager.active_chat_ids()
                    for chat_id in active_chats:
                        updated_pairs = int(by_chat.get(int(chat_id), 0) or 0)
                        if by_chat and updated_pairs <= 0:
                            continue
                        await _broadcast_manager.publish(
                            int(chat_id),
                            {
                                "type": "graph_delta",
                                "chat_id": int(chat_id),
                                "updated_pairs": updated_pairs if by_chat else updated,
                                "ts": _utc_now(),
                                "source": "social_graph_realtime",
                            },
                        )
            await asyncio.sleep(max(15, int(interval)))
        except asyncio.CancelledError:
            logger.info("Realtime broadcast loop cancelled")
            raise
        except Exception as e:
            logger.warning("Realtime broadcast loop error: %s", e)
            await asyncio.sleep(10)


async def start_realtime_worker() -> None:
    global _broadcast_task
    if _broadcast_task and not _broadcast_task.done():
        return
    _broadcast_task = asyncio.create_task(_realtime_broadcast_loop())


async def stop_realtime_worker() -> None:
    global _broadcast_task
    task = _broadcast_task
    _broadcast_task = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    await _broadcast_manager.shutdown()


async def get_realtime_stats_snapshot() -> dict:
    return await _broadcast_manager.stats()


@router.get("/stats")
async def realtime_stats(_auth=Depends(require_auth)):
    return {"ok": True, "stats": await _broadcast_manager.stats(), "ts": _utc_now()}


@router.websocket("/ws/{chat_id}")
async def websocket_endpoint(ws: WebSocket, chat_id: str):
    try:
        chat_id_int = int(str(chat_id).strip())
    except Exception:
        await ws.close(code=1008, reason="invalid_chat_id")
        return

    client = await _broadcast_manager.connect(ws, chat_id_int)
    try:
        while True:
            raw = await ws.receive_text()
            text = str(raw or "").strip()
            if text.lower() == "ping":
                await _broadcast_manager.enqueue_personal(
                    client,
                    {"type": "pong", "chat_id": int(chat_id_int), "ts": _utc_now()},
                )
                continue
            try:
                msg = json.loads(text)
            except Exception:
                continue
            if str(msg.get("type", "")).lower() == "ping":
                await _broadcast_manager.enqueue_personal(
                    client,
                    {"type": "pong", "chat_id": int(chat_id_int), "ts": _utc_now()},
                )
    except WebSocketDisconnect:
        pass
    finally:
        await _broadcast_manager.disconnect(client)
