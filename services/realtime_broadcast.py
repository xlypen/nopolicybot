from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
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
    last_slow_warning_ts: float = 0.0
    close_requested: bool = False
    close_code: int | None = None
    close_reason: str = ""
    max_queue_utilization: float = 0.0


class BroadcastManager:
    def __init__(
        self,
        *,
        queue_size: int = 64,
        heartbeat_sec: int = 25,
        slow_warn_threshold: float = 0.8,
        slow_warn_cooldown_sec: int = 20,
    ):
        self._queue_size = max(1, int(queue_size))
        self._heartbeat_sec = max(1, int(heartbeat_sec))
        self._slow_warn_threshold = min(0.99, max(0.5, float(slow_warn_threshold)))
        self._slow_warn_cooldown_sec = max(1, int(slow_warn_cooldown_sec))
        self._clients_by_chat: dict[int, set[_Client]] = {}
        self._lock = asyncio.Lock()
        self._redis_url = str(os.getenv("REDIS_URL", "") or "").strip()
        self._redis_channel_prefix = str(os.getenv("WS_REDIS_CHANNEL_PREFIX", "nopolicybot:ws") or "nopolicybot:ws").strip()
        self._redis_client = None
        self._redis_pubsub = None
        self._redis_listener_task: asyncio.Task | None = None
        self._redis_init_lock = asyncio.Lock()
        self._instance_id = str(uuid.uuid4())

    async def connect(self, ws: WebSocket, chat_id: int) -> _Client:
        await ws.accept()
        client = _Client(ws=ws, chat_id=int(chat_id), queue=asyncio.Queue(maxsize=self._queue_size))
        async with self._lock:
            self._clients_by_chat.setdefault(client.chat_id, set()).add(client)
        await self._ensure_redis_listener()
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

    async def shutdown(self) -> None:
        async with self._lock:
            clients = [c for bucket in self._clients_by_chat.values() for c in bucket]
        for client in clients:
            await self.disconnect(client)
        if self._redis_listener_task and not self._redis_listener_task.done():
            self._redis_listener_task.cancel()
            try:
                await self._redis_listener_task
            except BaseException:
                pass
        self._redis_listener_task = None
        try:
            if self._redis_pubsub is not None:
                await self._redis_pubsub.close()
        except Exception:
            pass
        self._redis_pubsub = None
        try:
            if self._redis_client is not None:
                await self._redis_client.aclose()
        except Exception:
            pass
        self._redis_client = None

    async def enqueue_personal(self, client: _Client, payload: dict[str, Any]) -> None:
        msg = json.dumps(payload, ensure_ascii=False)
        self._enqueue(client, msg)

    async def publish(self, chat_id: int, payload: dict[str, Any]) -> int:
        msg = json.dumps(payload, ensure_ascii=False)
        async with self._lock:
            clients = list(self._clients_by_chat.get(int(chat_id), set()))
        if clients and await self._publish_via_redis(int(chat_id), payload):
            return len(clients)
        for client in clients:
            self._enqueue(client, msg)
        return len(clients)

    async def publish_all(self, payload: dict[str, Any]) -> int:
        msg = json.dumps(payload, ensure_ascii=False)
        async with self._lock:
            clients = [c for bucket in self._clients_by_chat.values() for c in bucket]
        if clients and await self._publish_via_redis(None, payload):
            return len(clients)
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
            util_by_chat: dict[str, dict[str, float | int]] = {}
            for chat_id, bucket in self._clients_by_chat.items():
                chats[str(chat_id)] = len(bucket)
                total += len(bucket)
                ratios = []
                for client in bucket:
                    ratio = float(client.queue.qsize()) / float(max(1, self._queue_size))
                    ratios.append(ratio)
                if ratios:
                    util_by_chat[str(chat_id)] = {
                        "avg": round(sum(ratios) / len(ratios), 4),
                        "max": round(max(ratios), 4),
                        "clients": len(ratios),
                    }
            return {
                "connected_clients": total,
                "active_chats": len(chats),
                "clients_by_chat": chats,
                "queue_size": self._queue_size,
                "heartbeat_sec": self._heartbeat_sec,
                "ws_queue_utilization": util_by_chat,
                "redis_enabled": bool(self._redis_url),
            }

    def _enqueue(self, client: _Client, msg: str) -> None:
        q_cap = max(1, self._queue_size)
        q_size = int(client.queue.qsize())
        q_util = float(q_size) / float(q_cap)
        client.max_queue_utilization = max(float(client.max_queue_utilization), q_util)
        now = time.monotonic()
        if q_util >= self._slow_warn_threshold and (now - float(client.last_slow_warning_ts)) >= self._slow_warn_cooldown_sec:
            warn_payload = {
                "type": "slow_client_warning",
                "chat_id": int(client.chat_id),
                "queue_utilization": round(q_util, 4),
                "queue_size": q_size,
                "queue_capacity": q_cap,
                "ts": _utc_now(),
            }
            try:
                client.queue.put_nowait(json.dumps(warn_payload, ensure_ascii=False))
            except Exception:
                pass
            client.last_slow_warning_ts = now
        if client.queue.full():
            client.dropped_messages += 1
            client.close_requested = True
            client.close_code = 1008
            client.close_reason = "queue_overflow"
            logger.warning("WS queue overflow: chat=%s dropped=%s", client.chat_id, client.dropped_messages)
            return
        try:
            client.queue.put_nowait(msg)
        except Exception:
            client.dropped_messages += 1
            client.close_requested = True
            client.close_code = 1008
            client.close_reason = "queue_overflow"

    async def _publish_via_redis(self, chat_id: int | None, payload: dict[str, Any]) -> bool:
        if not await self._ensure_redis_listener():
            return False
        if self._redis_client is None:
            return False
        channel = f"{self._redis_channel_prefix}:all" if chat_id is None else f"{self._redis_channel_prefix}:chat:{int(chat_id)}"
        envelope = {"origin": self._instance_id, "chat_id": chat_id, "payload": payload, "ts": _utc_now()}
        try:
            await self._redis_client.publish(channel, json.dumps(envelope, ensure_ascii=False))
            return True
        except Exception as e:
            logger.warning("Redis publish failed: %s", e)
            return False

    async def _ensure_redis_listener(self) -> bool:
        if not self._redis_url:
            return False
        if self._redis_listener_task and not self._redis_listener_task.done():
            return True
        async with self._redis_init_lock:
            if self._redis_listener_task and not self._redis_listener_task.done():
                return True
            try:
                import redis.asyncio as redis_async
            except Exception:
                logger.warning("REDIS_URL is set but redis-py asyncio backend is unavailable")
                return False
            try:
                self._redis_client = redis_async.from_url(self._redis_url, decode_responses=True)
                self._redis_pubsub = self._redis_client.pubsub(ignore_subscribe_messages=True)
                await self._redis_pubsub.psubscribe(f"{self._redis_channel_prefix}:chat:*")
                await self._redis_pubsub.subscribe(f"{self._redis_channel_prefix}:all")
                self._redis_listener_task = asyncio.create_task(self._redis_listener_loop())
                logger.info("Realtime broadcast Redis pub/sub enabled")
                return True
            except Exception as e:
                logger.warning("Failed to initialize Redis pub/sub backend: %s", e)
                self._redis_client = None
                self._redis_pubsub = None
                self._redis_listener_task = None
                return False

    async def _redis_listener_loop(self) -> None:
        try:
            while True:
                if self._redis_pubsub is None:
                    await asyncio.sleep(0.2)
                    continue
                msg = await self._redis_pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not msg:
                    await asyncio.sleep(0.05)
                    continue
                kind = str(msg.get("type", "")).lower()
                if kind not in {"message", "pmessage"}:
                    continue
                data_raw = msg.get("data")
                if not isinstance(data_raw, str):
                    continue
                try:
                    envelope = json.loads(data_raw)
                except Exception:
                    continue
                if not isinstance(envelope, dict):
                    continue
                payload = envelope.get("payload")
                if not isinstance(payload, dict):
                    continue
                text = json.dumps(payload, ensure_ascii=False)
                chat_id = envelope.get("chat_id")
                if chat_id is None:
                    await self._fanout_local_all(text)
                else:
                    await self._fanout_local_chat(int(chat_id), text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Realtime Redis listener stopped: %s", e)

    async def _fanout_local_chat(self, chat_id: int, msg: str) -> int:
        async with self._lock:
            clients = list(self._clients_by_chat.get(int(chat_id), set()))
        for client in clients:
            self._enqueue(client, msg)
        return len(clients)

    async def _fanout_local_all(self, msg: str) -> int:
        async with self._lock:
            clients = [c for bucket in self._clients_by_chat.values() for c in bucket]
        for client in clients:
            self._enqueue(client, msg)
        return len(clients)

    async def _sender_loop(self, client: _Client) -> None:
        try:
            while True:
                if client.close_requested:
                    try:
                        await client.ws.send_text(
                            json.dumps(
                                {
                                    "type": "disconnect",
                                    "reason": str(client.close_reason or "policy_violation"),
                                    "chat_id": int(client.chat_id),
                                    "ts": _utc_now(),
                                },
                                ensure_ascii=False,
                            )
                        )
                    except Exception:
                        pass
                    try:
                        await client.ws.close(code=int(client.close_code or 1008), reason=str(client.close_reason or "policy_violation"))
                    except Exception:
                        pass
                    break
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
                                "queue_utilization": round(float(client.queue.qsize()) / float(max(1, self._queue_size)), 4),
                            },
                            ensure_ascii=False,
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("WS sender loop stopped for chat %s: %s", client.chat_id, e)
