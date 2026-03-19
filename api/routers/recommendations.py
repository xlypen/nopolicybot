"""Recommendations API."""

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from api.dependencies import require_auth
from services.recommendations import build_recommendations
from services.audit_log import write_event

router = APIRouter()


def _parse_chat_id(chat_id: str | None) -> tuple[int | None, str | None]:
    raw = (chat_id or "all").strip().lower()
    if raw == "all":
        return None, None
    if raw.lstrip("-").isdigit():
        return int(raw), None
    return None, "invalid chat_id"


@router.get("")
async def get_recommendations(
    chat_id: str | None = Query(default="all"),
    days: int = Query(default=30, ge=1, le=90),
    limit: int = Query(default=20, ge=1, le=100),
    _auth=Depends(require_auth),
):
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    payload = await asyncio.to_thread(build_recommendations, cid, days=days, limit=limit)
    return {"ok": True, "recommendations": payload}


@router.post("/mark-done")
async def post_mark_done(
    body: dict = Body(default_factory=dict),
    _auth=Depends(require_auth),
):
    item = body.get("item") if isinstance(body.get("item"), dict) else {}
    chat_raw = str(body.get("chat_id", "all") or "all").strip().lower()
    completed = bool(body.get("completed", True))
    write_event(
        "recommendation_marked_done",
        severity="info",
        source="api_v2",
        payload={
            "chat_id": chat_raw,
            "completed": completed,
            "type": str(item.get("type") or ""),
            "priority": str(item.get("priority") or ""),
            "user_id": int(item.get("user_id", 0) or 0) if str(item.get("user_id", "")).lstrip("-").isdigit() else None,
            "reason": str(item.get("reason") or "")[:240],
            "action": str(item.get("action") or "")[:240],
        },
    )
    return {"ok": True}
