"""Settings & Ops API — settings, chat-mode, reset-political-count."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Query

from api.dependencies import require_auth
import bot_settings

router = APIRouter()
logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESET_POLITICAL_COUNT_PATH = _PROJECT_ROOT / "reset_political_count.json"
MODE_CHANGES_LOG = _PROJECT_ROOT / "mode_changes.log"


def _chat_mode_descriptions() -> dict[str, str]:
    try:
        return {
            k: v.get("_desc", v.get("_label", k))
            for k, v in bot_settings.CHAT_MODE_PRESETS.items()
        } | {"custom": "Ручные переопределения в настройках чата"}
    except Exception:
        return {
            "default": "Глобальные настройки",
            "soft": "Реакции 1–5, замечания с 5-го",
            "active": "Реакции с 1-го, замечания с 3-го",
            "beast": "Максимум с 1-го",
            "custom": "Ручные переопределения",
        }


def _run_sync(fn, *args, **kwargs):
    return asyncio.to_thread(fn, *args, **kwargs)


@router.get("/settings")
async def get_settings(_auth=Depends(require_auth)):
    data = await _run_sync(bot_settings.get_all)
    return {"ok": True, "settings": data}


@router.post("/settings")
async def post_settings(body: dict = Body(default_factory=dict), _auth=Depends(require_auth)):
    updates = {}
    for k, v in body.items():
        if k in bot_settings.DEFAULTS and k != "chat_settings":
            updates[k] = v
    if updates:
        await _run_sync(bot_settings.set_all, updates)
    data = await _run_sync(bot_settings.get_all)
    return {"ok": True, "settings": data}


@router.get("/chat-mode")
async def get_chat_mode(chat_id: str = Query(...), _auth=Depends(require_auth)):
    try:
        cid = int(chat_id)
    except (ValueError, TypeError):
        return {"ok": False, "error": "chat_id должен быть числом"}
    mode = await _run_sync(bot_settings.get_chat_mode, cid)
    descriptions = _chat_mode_descriptions()
    return {
        "ok": True,
        "chat_id": cid,
        "mode": mode,
        "label": descriptions.get(mode, mode),
        "descriptions": descriptions,
    }


@router.post("/chat-mode")
async def post_chat_mode(body: dict = Body(default_factory=dict), _auth=Depends(require_auth)):
    from datetime import datetime

    chat_id = body.get("chat_id")
    mode = body.get("mode")
    if chat_id is None:
        return {"ok": False, "error": "Нужен chat_id"}
    try:
        cid = int(chat_id)
    except (ValueError, TypeError):
        return {"ok": False, "error": "chat_id должен быть числом"}
    if mode not in ("default", "soft", "active", "beast"):
        return {"ok": False, "error": "Нужен mode: default, soft, active или beast"}
    ok = await _run_sync(bot_settings.set_chat_mode, cid, mode)
    if ok:
        preset = bot_settings.CHAT_MODE_PRESETS.get(mode, {})
        label = preset.get("_label", mode)
        msg = f"[{datetime.now().strftime('%H:%M:%S')}] Чат {cid}: режим → «{label}»"
        logger.info(msg)
        try:
            MODE_CHANGES_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(MODE_CHANGES_LOG, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
        return {"ok": True, "mode": mode, "label": label, "descriptions": _chat_mode_descriptions()}
    return {"ok": False, "error": "Не удалось применить режим"}


@router.post("/reset-political-count")
async def post_reset_political_count(body: dict = Body(default_factory=dict), _auth=Depends(require_auth)):
    chat_id = body.get("chat_id")
    if chat_id is None:
        return {"ok": False, "error": "Нужен chat_id"}
    try:
        cid = int(chat_id)
    except (ValueError, TypeError):
        return {"ok": False, "error": "chat_id должен быть числом"}
    cid_str = str(cid)
    existing = []
    if RESET_POLITICAL_COUNT_PATH.exists():
        try:
            fdata = json.loads(RESET_POLITICAL_COUNT_PATH.read_text(encoding="utf-8"))
            existing = list(fdata.get("chat_ids") or [])
        except Exception:
            pass
    if cid_str not in existing:
        existing.append(cid_str)
    RESET_POLITICAL_COUNT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESET_POLITICAL_COUNT_PATH.write_text(
        json.dumps({"chat_ids": existing}, ensure_ascii=False), encoding="utf-8"
    )
    return {"ok": True, "message": f"Сброс для чата {cid} запланирован. Применится при следующем сообщении в чате."}
