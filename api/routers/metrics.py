"""Metrics API — user metrics, chat health."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Path as FPath, Query

from api.dependencies import require_auth
from services.marketing_metrics import get_chat_health, get_user_metrics

router = APIRouter()


def _run_sync(fn, *args, **kwargs):
    return asyncio.to_thread(fn, *args, **kwargs)


@router.get("/user/{user_id}")
async def get_user_metrics_endpoint(
    user_id: str = FPath(...),
    chat_id: str | None = Query(default="all"),
    days: int = Query(default=30, ge=1, le=90),
    _auth=Depends(require_auth),
):
    if not str(user_id).lstrip("-").isdigit():
        return {"ok": False, "error": "invalid user_id"}
    chat_raw = (chat_id or "all").strip().lower()
    cid = None if chat_raw == "all" else (int(chat_raw) if chat_raw.lstrip("-").isdigit() else None)
    if chat_raw != "all" and cid is None:
        return {"ok": False, "error": "invalid chat_id"}
    payload = await _run_sync(get_user_metrics, int(user_id), chat_id=cid, days=days)
    return {"ok": True, "metrics": payload}


@router.get("/chat/{chat_id}/health")
async def get_chat_health_endpoint(
    chat_id: str = FPath(...),
    days: int = Query(default=30, ge=1, le=90),
    _auth=Depends(require_auth),
):
    if not str(chat_id).lstrip("-").isdigit():
        return {"ok": False, "error": "invalid chat_id"}
    payload = await _run_sync(get_chat_health, int(chat_id), days=days)
    return {"ok": True, "health": payload}
