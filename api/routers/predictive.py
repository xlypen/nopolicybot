"""Predictive models API."""

import asyncio

from fastapi import APIRouter, Depends, Query

from api.dependencies import require_auth
from services.predictive_models import predict_overview

router = APIRouter()


def _parse_chat_id(chat_id: str | None) -> tuple[int | None, str | None]:
    raw = (chat_id or "all").strip().lower()
    if raw == "all":
        return None, None
    if raw.lstrip("-").isdigit():
        return int(raw), None
    return None, "invalid chat_id"


@router.get("/overview")
async def get_predictive_overview(
    chat_id: str | None = Query(default="all"),
    horizon_days: int = Query(default=7, ge=1, le=30),
    lookback_days: int = Query(default=30, ge=7, le=180),
    _auth=Depends(require_auth),
):
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    payload = await asyncio.to_thread(
        predict_overview, cid, horizon_days=horizon_days, lookback_days=lookback_days
    )
    return {"ok": True, "overview": payload}
