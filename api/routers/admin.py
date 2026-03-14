"""Admin dashboard API — dashboard, community-structure, leaderboard, at-risk."""

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from api.dependencies import require_auth
from services.admin_dashboards import (
    build_at_risk_users_dashboard,
    build_chat_health_dashboard,
    build_community_structure_dashboard,
    build_user_leaderboard_dashboard,
)
from services.audit_log import write_event

router = APIRouter()


def _parse_chat_id(chat_id: str | None) -> tuple[int | None, str | None]:
    raw = (chat_id or "all").strip().lower()
    if raw == "all":
        return None, None
    if raw.lstrip("-").isdigit():
        return int(raw), None
    return None, "invalid chat_id"


def _run_sync(fn, *args, **kwargs) -> Any:
    return asyncio.to_thread(fn, *args, **kwargs)


@router.get("/dashboard")
async def get_admin_dashboard(
    chat_id: str | None = Query(default="all"),
    days: int = Query(default=30, ge=1, le=180),
    _auth=Depends(require_auth),
):
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    payload = await _run_sync(build_chat_health_dashboard, cid, days=days)
    return {"ok": True, "dashboard": payload}


@router.get("/community-structure")
async def get_community_structure(
    chat_id: str | None = Query(default="all"),
    period: str = Query(default="30d"),
    limit: int = Query(default=1200, ge=200, le=5000),
    _auth=Depends(require_auth),
):
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    payload = await _run_sync(
        build_community_structure_dashboard, cid, period=period, limit=limit
    )
    return {"ok": True, "community": payload}


@router.get("/leaderboard")
async def get_leaderboard(
    chat_id: str | None = Query(default="all"),
    metric: str = Query(default="engagement"),
    days: int = Query(default=30, ge=1, le=180),
    limit: int = Query(default=10, ge=1, le=100),
    _auth=Depends(require_auth),
):
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    payload = await _run_sync(
        build_user_leaderboard_dashboard,
        cid,
        metric=metric,
        limit=limit,
        days=days,
    )
    return {"ok": True, "leaderboard": payload}


@router.get("/at-risk-users")
async def get_at_risk_users(
    chat_id: str | None = Query(default="all"),
    days: int = Query(default=30, ge=1, le=180),
    limit: int = Query(default=30, ge=1, le=200),
    threshold: float = Query(default=0.6, ge=0.0, le=1.0),
    _auth=Depends(require_auth),
):
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    payload = await _run_sync(
        build_at_risk_users_dashboard,
        cid,
        threshold=threshold,
        days=days,
        limit=limit,
    )
    return {"ok": True, "at_risk": payload}


@router.post("/at-risk-action")
async def post_at_risk_action(
    body: dict = Body(default_factory=dict),
    _auth=Depends(require_auth),
):
    action = str(body.get("action") or "").strip().lower()
    user_id_raw = str(body.get("user_id") or "").strip()
    chat_id = str(body.get("chat_id") or "all").strip().lower()
    if action not in {"dm", "clear_flag"}:
        return {"ok": False, "error": "invalid action"}
    if not user_id_raw.lstrip("-").isdigit():
        return {"ok": False, "error": "invalid user_id"}
    user_id = int(user_id_raw)
    write_event(
        "admin_at_risk_action",
        severity="info",
        source="api_v2",
        payload={"action": action, "chat_id": chat_id, "user_id": user_id},
    )
    return {
        "ok": True,
        "action": action,
        "user_id": user_id,
        "chat_id": chat_id,
        "queued": action == "dm",
    }
