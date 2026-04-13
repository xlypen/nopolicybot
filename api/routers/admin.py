"""Admin dashboard API — dashboard, community-structure, leaderboard, at-risk, log-tail, prompts, topic-policies."""

import asyncio
import logging
import os
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
logger = logging.getLogger(__name__)

# Кэш ответов админки: короче TTL — виджеты ближе к «сейчас»; тяжёлые расчёты не дублируем каждую секунду.
_ADMIN_CACHE_TTL_SEC = int(os.getenv("ADMIN_DASHBOARD_CACHE_TTL_SEC", "25") or "25")
_admin_cache: dict[tuple, tuple[Any, float]] = {}
# Жёсткий предел на расчёт одного виджета (иначе прокси/браузер висят на «Загрузка…»).
_ADMIN_BUILD_TIMEOUT_SEC = float(os.getenv("ADMIN_DASHBOARD_BUILD_TIMEOUT_SEC", "90") or "90")


def _cache_key(*parts: Any) -> tuple:
    return tuple(parts)


def _cached(key: tuple, builder: Callable[[], Any], *, skip_cache: bool = False) -> Any:
    if skip_cache:
        return builder()
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


def _load_users_db_aware() -> dict[str, dict]:
    """Загружает пользователей из storage (DB) и использует JSON только как fallback."""
    users: dict[str, dict] = {}
    try:
        from services.sqlite_storage import get_storage

        st = get_storage()
        if st:
            for uid, profile in (st.iter_user_profiles() or []):
                try:
                    key = str(int(uid))
                except Exception:
                    key = str(uid).strip()
                if not key:
                    continue
                users[key] = profile if isinstance(profile, dict) else {}
    except Exception as e:
        logger.debug("load users from storage failed: %s", e)
    if users:
        return users
    try:
        from api.routers.portrait import _load_users

        raw_users = (_load_users() or {}).get("users") or {}
        if isinstance(raw_users, dict):
            for uid, profile in raw_users.items():
                key = str(uid).strip()
                if key:
                    users[key] = profile if isinstance(profile, dict) else {}
    except Exception as e:
        logger.debug("load users from JSON fallback failed: %s", e)
    return users


async def _run_cached_build(key: tuple, builder: Callable[[], Any], *, skip_cache: bool = False) -> Any:
    """Считает payload в thread pool с лимитом по времени."""
    try:
        return await asyncio.wait_for(
            _run_sync(_cached, key, builder, skip_cache=skip_cache),
            timeout=max(15.0, _ADMIN_BUILD_TIMEOUT_SEC),
        )
    except asyncio.TimeoutError:
        if not skip_cache and key in _admin_cache:
            del _admin_cache[key]
        raise


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
    refresh: bool = Query(default=False, description="Пропустить кэш и пересчитать"),
    _auth=Depends(require_auth),
):
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    try:
        key = _cache_key("dashboard", cid, days)
        payload = await _run_cached_build(
            key,
            lambda: build_chat_health_dashboard(cid, days=days),
            skip_cache=refresh,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={
                "ok": False,
                "error": f"Таймаут расчёта дашборда (>{int(_ADMIN_BUILD_TIMEOUT_SEC)} с). Попробуйте позже или чат «все».",
            },
        )
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
    limit: int = Query(default=800, ge=200, le=5000),
    refresh: bool = Query(default=False),
    _auth=Depends(require_auth),
):
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    try:
        key = _cache_key("community", cid, period, limit)
        payload = await _run_cached_build(
            key,
            lambda: build_community_structure_dashboard(cid, period=period, limit=limit),
            skip_cache=refresh,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={
                "ok": False,
                "error": f"Таймаут расчёта структуры сообщества (>{int(_ADMIN_BUILD_TIMEOUT_SEC)} с). Уменьшите limit в запросе.",
            },
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
    refresh: bool = Query(default=False),
    _auth=Depends(require_auth),
):
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    if refresh:
        from services.marketing_metrics import invalidate_graph_rows_cache

        invalidate_graph_rows_cache()
    try:
        key = _cache_key("leaderboard", cid, metric, days, limit)
        payload = await _run_cached_build(
            key,
            lambda: build_user_leaderboard_dashboard(cid, metric=metric, limit=limit, days=days),
            skip_cache=refresh,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={"ok": False, "error": f"Таймаут расчёта рейтинга (>{int(_ADMIN_BUILD_TIMEOUT_SEC)} с)."},
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
    refresh: bool = Query(default=False),
    _auth=Depends(require_auth),
):
    cid, err = _parse_chat_id(chat_id)
    if err:
        return {"ok": False, "error": err}
    try:
        key = _cache_key("at_risk", cid, days, limit, threshold)
        payload = await _run_cached_build(
            key,
            lambda: build_at_risk_users_dashboard(cid, threshold=threshold, days=days, limit=limit),
            skip_cache=refresh,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            status_code=504,
            content={"ok": False, "error": f"Таймаут расчёта at-risk (>{int(_ADMIN_BUILD_TIMEOUT_SEC)} с)."},
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
    _ALLOWED_LOG_DIRS = ("/var/log/", "/opt/telegram-political-monitor-bot/data/", "/tmp/")
    path = Path(source).resolve()
    if not any(str(path).startswith(d) for d in _ALLOWED_LOG_DIRS):
        return {"ok": False, "error": "file path not in allowed directories"}
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


@router.get("/classification-axes")
async def get_classification_axes(_auth=Depends(require_auth)):
    return {"ok": False, "error": "classification axes disabled — feature not in use"}


@router.put("/classification-axes")
async def put_classification_axes(body: dict = Body(default_factory=dict), _auth=Depends(require_auth)):
    return {"ok": False, "error": "classification axes disabled — feature not in use"}


@router.get("/classification-metrics")
async def get_classification_metrics(
    chat_id: str = Query(default="all"),
    _auth=Depends(require_auth),
):
    return {"ok": False, "error": "classification axes disabled — feature not in use"}


@router.post("/classification-run")
async def post_classification_run(body: dict = Body(default_factory=dict), _auth=Depends(require_auth)):
    return {"ok": False, "error": "classification axes disabled — feature not in use"}

    # --- disabled code below ---
    from ai_analyzer import classify_user_on_axis
    from services.classification_axes import get_axis_by_id, parse_categories, user_needs_axis_run
    from user_stats import get_user, get_user_messages_archive, get_users_in_chat, set_user_axis_classification

    axis_id = str(body.get("axis_id") or "").strip().lower()
    axis = get_axis_by_id(axis_id)
    if not axis or not axis.get("enabled"):
        return {"ok": False, "error": "ось не найдена или выключена"}
    raw_chat = str(body.get("chat_id", "all") or "all").strip()
    chat_id: int | None = None
    if raw_chat != "all":
        if not raw_chat.lstrip("-").isdigit():
            return {"ok": False, "error": "некорректный chat_id"}
        chat_id = int(raw_chat)

    raw_fa = body.get("from_archive")
    if isinstance(raw_fa, str):
        from_archive = raw_fa.strip().lower() in ("1", "true", "yes", "on")
    else:
        from_archive = bool(raw_fa)
    mode = str(body.get("mode") or "").strip().lower()
    if from_archive or mode in ("archive", "full", "full_archive"):
        only_unknown = False
    else:
        only_unknown = bool(body.get("only_unknown", True))

    # «Из архива» — больше участников за проход (пересчёт по полному архиву сообщений)
    default_limit = 80 if not only_unknown else 40
    limit = max(1, min(200, int(body.get("limit") or default_limit)))

    labels = parse_categories(str(axis.get("categories") or ""))
    if len(labels) < 2:
        return {"ok": False, "error": "у оси меньше 2 категорий"}

    instruction = str(axis.get("instruction") or "").strip()
    sync_rank = bool(axis.get("sync_with_rank"))

    users = _load_users_db_aware()
    chat_members: set[str] | None = None
    if chat_id is not None:
        chat_members = {str(int(x)) for x in (await asyncio.to_thread(get_users_in_chat, chat_id) or [])}

    candidates: list[int] = []
    for uid, row in users.items():
        uid_str = str(uid or "").strip()
        if not uid_str.lstrip("-").isdigit():
            continue
        if chat_members is not None and uid_str not in chat_members:
            continue
        u = row or {}
        if only_unknown and not user_needs_axis_run(u, axis_id, axis):
            continue
        candidates.append(int(uid_str))
    candidates = candidates[:limit]

    processed = 0
    skipped_no_messages = 0
    failed = 0
    for uid in candidates:
        try:
            msgs = await asyncio.to_thread(get_user_messages_archive, uid, chat_id)
            if not msgs:
                skipped_no_messages += 1
                continue
            u = await asyncio.to_thread(get_user, uid)
            display_name = str((u or {}).get("display_name") or uid)
            val, _note = await asyncio.to_thread(
                classify_user_on_axis,
                msgs,
                display_name,
                instruction,
                labels,
            )
            await asyncio.to_thread(
                set_user_axis_classification,
                uid,
                axis_id,
                val,
                sync_rank=sync_rank,
            )
            processed += 1
        except Exception:
            logger.exception(
                "classification-run failed: axis_id=%s chat_id=%s user_id=%s",
                axis_id,
                chat_id,
                uid,
            )
            failed += 1

    return {
        "ok": True,
        "axis_id": axis_id,
        "chat_id": "all" if chat_id is None else chat_id,
        "from_archive": not only_unknown,
        "only_unknown": only_unknown,
        "candidates": len(candidates),
        "processed": processed,
        "skipped_no_messages": skipped_no_messages,
        "failed": failed,
    }
