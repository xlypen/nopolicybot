"""Admin dashboard API — dashboard, community-structure, leaderboard, at-risk, log-tail, prompts, topic-policies."""

import asyncio
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from api.dependencies import require_auth
from services.admin_dashboards import (
    build_at_risk_users_dashboard,
    build_chat_health_dashboard,
    build_community_structure_dashboard,
    build_user_leaderboard_dashboard,
    build_users_list,
)
from services.audit_log import write_event

router = APIRouter()

# Кэш ответов админки (TTL 45 сек), чтобы дашборд не грузился минутами при каждом открытии/переключении чата
_ADMIN_CACHE_TTL_SEC = 45
_admin_cache: dict[tuple, tuple[Any, float]] = {}


def _cache_key(*parts: Any) -> tuple:
    return tuple(parts)


def _cached(key: tuple, builder: Callable[[], Any]) -> Any:
    now = time.monotonic()
    if key in _admin_cache:
        val, expiry = _admin_cache[key]
        if now < expiry:
            return val
        del _admin_cache[key]
    val = builder()
    _admin_cache[key] = (val, now + _ADMIN_CACHE_TTL_SEC)
    return val


def _parse_chat_id(chat_id: str | None) -> tuple[int | None, str | None]:
    raw = (chat_id or "all").strip().lower()
    if raw == "all":
        return None, None
    if raw.lstrip("-").isdigit():
        return int(raw), None
    return None, "invalid chat_id"


def _run_sync(fn, *args, **kwargs) -> Any:
    return asyncio.to_thread(fn, *args, **kwargs)


def start_admin_cache_warmer() -> None:
    """Запуск подогрева кэша админки. Пока заглушка — кэш заполняется по первому запросу."""
    pass


async def stop_admin_cache_warmer() -> None:
    """Остановка подогрева кэша при shutdown. Пока заглушка."""
    pass


@router.get("/dashboard")
async def get_admin_dashboard(
    chat_id: str | None = Query(default="all"),
    days: int = Query(default=30, ge=1, le=180),
    _auth=Depends(require_auth),
):
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    try:
        key = _cache_key("dashboard", cid, days)
        payload = await _run_sync(_cached, key, lambda: build_chat_health_dashboard(cid, days=days))
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": f"dashboard: {e!s}"},
        )
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
    try:
        key = _cache_key("community", cid, period, limit)
        payload = await _run_sync(
            _cached, key, lambda: build_community_structure_dashboard(cid, period=period, limit=limit)
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": f"community: {e!s}"},
        )
    return {"ok": True, "community": payload}


@router.get("/users")
async def get_users(
    chat_id: str | None = Query(default="all"),
    limit: int = Query(default=300, ge=1, le=500),
    _auth=Depends(require_auth),
):
    """Список пользователей для выпадающих списков: [{id, name}, ...]."""
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    users = await _run_sync(build_users_list, cid, limit=limit)
    return {"ok": True, "users": users}


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
    try:
        key = _cache_key("leaderboard", cid, metric, days, limit)
        payload = await _run_sync(
            _cached, key, lambda: build_user_leaderboard_dashboard(cid, metric=metric, limit=limit, days=days)
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": f"leaderboard: {e!s}"},
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
    try:
        key = _cache_key("at_risk", cid, days, limit, threshold)
        payload = await _run_sync(
            _cached, key, lambda: build_at_risk_users_dashboard(cid, threshold=threshold, days=days, limit=limit)
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": f"at_risk: {e!s}"},
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


@router.get("/log-tail")
async def get_log_tail(
    lines: int = Query(default=120, ge=20, le=500),
    source: str = Query(default="telegram-bot.service"),
    _auth=Depends(require_auth),
):
    if source.endswith(".service"):
        allowed = {"telegram-bot.service", "telegram-bot-admin.service", "telegram-bot-api.service"}
        if source not in allowed:
            source = "telegram-bot.service"
        try:
            cp = await asyncio.to_thread(
                subprocess.run,
                ["journalctl", "-u", source, "-n", str(lines), "--no-pager", "-o", "cat"],
                capture_output=True,
                text=True,
                check=False,
            )
            content = (cp.stdout or "").splitlines()
        except Exception:
            content = []
        return {"ok": True, "lines": content[-lines:], "source": source, "kind": "systemd"}
    path = Path(source)
    if not path.exists():
        return {"ok": True, "lines": [], "source": str(path), "kind": "file"}
    try:
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
        lines_list = content.splitlines()
    except Exception:
        lines_list = []
    return {"ok": True, "lines": lines_list[-lines:], "source": str(path), "kind": "file"}


@router.get("/prompts")
async def get_prompts(_auth=Depends(require_auth)):
    from ai.prompts import get_all_prompts

    return {"ok": True, "prompts": await asyncio.to_thread(get_all_prompts)}


@router.post("/prompts")
async def post_prompts(body: dict = Body(default_factory=dict), _auth=Depends(require_auth)):
    from ai.prompts import get_all_prompts, set_prompt

    name = str(body.get("name") or "").strip()
    value = str(body.get("value") or "")
    if not name:
        return {"ok": False, "error": "name is required"}
    await asyncio.to_thread(set_prompt, name, value)
    return {"ok": True, "prompts": await asyncio.to_thread(get_all_prompts)}


@router.delete("/prompts")
async def delete_prompts(body: dict = Body(default_factory=dict), _auth=Depends(require_auth)):
    from ai.prompts import get_all_prompts, reset_prompts

    names = body.get("names")
    names_list = [str(x) for x in names if str(x).strip()] if isinstance(names, list) else None
    prompts = await asyncio.to_thread(reset_prompts, names_list)
    return {"ok": True, "prompts": prompts}


@router.get("/topic-policies")
async def get_topic_policies(_auth=Depends(require_auth)):
    from services.topic_policies import get_primary_topic, get_topic_policies

    primary = await asyncio.to_thread(get_primary_topic)
    policies = await asyncio.to_thread(get_topic_policies)
    return {"ok": True, "primary_topic": primary, "policies": policies}


@router.post("/topic-policies")
async def post_topic_policies(body: dict = Body(default_factory=dict), _auth=Depends(require_auth)):
    from services.topic_policies import get_primary_topic, get_topic_policies, set_primary_topic, set_topic_policy

    primary_topic = body.get("primary_topic")
    if primary_topic is not None:
        await asyncio.to_thread(set_primary_topic, str(primary_topic))
    name = str(body.get("name") or "").strip().lower()
    patch = body.get("patch")
    if name and isinstance(patch, dict):
        await asyncio.to_thread(set_topic_policy, name, patch)
    return {"ok": True, "primary_topic": await asyncio.to_thread(get_primary_topic), "policies": await asyncio.to_thread(get_topic_policies)}


@router.delete("/topic-policies")
async def delete_topic_policies(body: dict = Body(default_factory=dict), _auth=Depends(require_auth)):
    from services.topic_policies import get_primary_topic, get_topic_policies, reset_topic_policies

    names = body.get("names")
    names_list = [str(x) for x in names] if isinstance(names, list) else None
    policies = await asyncio.to_thread(reset_topic_policies, names_list)
    return {"ok": True, "primary_topic": await asyncio.to_thread(get_primary_topic), "policies": policies}
