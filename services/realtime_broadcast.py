from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(eq=False, slots=True)
class _Client:
    ws: WebSocket
    chat_id: int
    queue: asyncio.Queue[str]
    sender_task: asyncio.Task | None = None
    dropped_messages: int = 0
    connected_at: str = field(default_factory=_utc_now)


class BroadcastManager:
    def __init__(self, *, queue_size: int = 64, heartbeat_sec: int = 25):
        self._queue_size = max(1, int(queue_size))
        self._heartbeat_sec = max(1, int(heartbeat_sec))
        self._clients_by_chat: dict[int, set[_Client]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, chat_id: int) -> _Client:
        await ws.accept()
        client = _Client(ws=ws, chat_id=int(chat_id), queue=asyncio.Queue(maxsize=self._queue_size))
        async with self._lock:
            self._clients_by_chat.setdefault(client.chat_id, set()).add(client)
        client.sender_task = asyncio.create_task(self._sender_loop(client))
        await self.enqueue_personal(
            client,
            {
                "type": "connected",
                "chat_id": client.chat_id,
                "ts": _utc_now(),
            },
        )
        return client

    async def disconnect(self, client: _Client) -> None:
        async with self._lock:
            bucket = self._clients_by_chat.get(client.chat_id)
            if bucket and client in bucket:
                bucket.remove(client)
                if not bucket:
                    self._clients_by_chat.pop(client.chat_id, None)
        if client.sender_task:
            client.sender_task.cancel()
            try:
                await client.sender_task
            except BaseException:
                pass

    async def enqueue_personal(self, client: _Client, payload: dict[str, Any]) -> None:
        msg = json.dumps(payload, ensure_ascii=False)
        self._enqueue(client, msg)

    async def publish(self, chat_id: int, payload: dict[str, Any]) -> int:
        msg = json.dumps(payload, ensure_ascii=False)
        async with self._lock:
            clients = list(self._clients_by_chat.get(int(chat_id), set()))
        for client in clients:
            self._enqueue(client, msg)
        return len(clients)

    async def publish_all(self, payload: dict[str, Any]) -> int:
        msg = json.dumps(payload, ensure_ascii=False)
        async with self._lock:
            clients = [c for bucket in self._clients_by_chat.values() for c in bucket]
        for client in clients:
            self._enqueue(client, msg)
        return len(clients)

    async def active_chat_ids(self) -> list[int]:
        async with self._lock:
            return sorted(self._clients_by_chat.keys())

    async def stats(self) -> dict[str, Any]:
        async with self._lock:
            chats = {}
            total = 0
            for chat_id, bucket in self._clients_by_chat.items():
                chats[str(chat_id)] = len(bucket)
                total += len(bucket)
            return {
                "connected_clients": total,
                "active_chats": len(chats),
                "clients_by_chat": chats,
                "queue_size": self._queue_size,
                "heartbeat_sec": self._heartbeat_sec,
            }

    def _enqueue(self, client: _Client, msg: str) -> None:
        if client.queue.full():
            try:
                client.queue.get_nowait()
                client.dropped_messages += 1
            except Exception:
                pass
        try:
            client.queue.put_nowait(msg)
        except Exception:
            client.dropped_messages += 1

    async def _sender_loop(self, client: _Client) -> None:
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(client.queue.get(), timeout=self._heartbeat_sec)
                    await client.ws.send_text(msg)
                except asyncio.TimeoutError:
                    await client.ws.send_text(
                        json.dumps(
                            {
                                "type": "heartbeat",
                                "chat_id": client.chat_id,
                                "ts": _utc_now(),
                                "dropped_messages": int(client.dropped_messages),
                            },
                            ensure_ascii=False,
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("WS sender loop stopped for chat %s: %s", client.chat_id, e)
