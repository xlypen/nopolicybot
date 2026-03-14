from __future__ import annotations

import asyncio
import json

import pytest

from services.realtime_broadcast import BroadcastManager


class _FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent: list[str] = []
        self.closed_code: int | None = None
        self.closed_reason: str | None = None

    async def accept(self):
        self.accepted = True

    async def send_text(self, text: str):
        self.sent.append(str(text))

    async def close(self, code: int = 1000, reason: str = ""):
        self.closed_code = int(code)
        self.closed_reason = str(reason or "")


@pytest.mark.asyncio
async def test_broadcast_manager_publish_to_chat_only():
    manager = BroadcastManager(queue_size=8, heartbeat_sec=60)
    ws1 = _FakeWebSocket()
    ws2 = _FakeWebSocket()
    c1 = await manager.connect(ws1, 100)
    c2 = await manager.connect(ws2, 200)
    try:
        n = await manager.publish(100, {"type": "graph_delta", "chat_id": 100})
        assert n == 1
        await asyncio.sleep(0.05)
        assert any(json.loads(x).get("type") == "graph_delta" for x in ws1.sent)
        assert not any(json.loads(x).get("type") == "graph_delta" for x in ws2.sent)
    finally:
        await manager.disconnect(c1)
        await manager.disconnect(c2)


@pytest.mark.asyncio
async def test_broadcast_manager_disconnects_on_queue_overflow():
    manager = BroadcastManager(queue_size=1, heartbeat_sec=60)
    ws = _FakeWebSocket()
    client = await manager.connect(ws, 42)
    try:
        await asyncio.sleep(0.05)
        await manager.enqueue_personal(client, {"type": "m1"})
        await manager.enqueue_personal(client, {"type": "m2"})
        await asyncio.sleep(0.1)
        types = [json.loads(x).get("type") for x in ws.sent]
        assert "m1" in types
        assert client.close_requested is True
        assert ws.closed_code == 1008
        assert client.dropped_messages >= 1
    finally:
        await manager.disconnect(client)

