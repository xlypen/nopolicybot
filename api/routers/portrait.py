"""Portrait API — build portrait, classify, status, clear cache, image generate."""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Path as FPath, Query

from api.dependencies import require_auth

router = APIRouter()
logger = logging.getLogger(__name__)

# In-process state (same as Flask had)
_portrait_building: set[str] = set()
_portrait_image_generating: set[str] = set()

USERS_JSON = Path(__file__).resolve().parent.parent.parent / "user_stats.json"


def _load_users() -> dict:
    if not USERS_JSON.exists():
        return {"users": {}}
    try:
        data = json.loads(USERS_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and "users" in data else {"users": {}}
    except Exception:
        return {"users": {}}


def _run_sync(fn, *args, **kwargs):
    return asyncio.to_thread(fn, *args, **kwargs)


@router.get("/user/{user_id}/portrait-image-status")
async def get_portrait_image_status(
    user_id: str = FPath(...),
    _auth=Depends(require_auth),
):
    return {"generating": user_id in _portrait_image_generating}


@router.post("/portrait-clear-cache")
async def post_portrait_clear_cache(_auth=Depends(require_auth)):
    try:
        from services.portrait_image import clear_portrait_model_cache

        ok = await _run_sync(clear_portrait_model_cache)
        return {"ok": True, "cleared": ok}
    except Exception as e:
        logger.exception("Очистка кеша портретов: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/user/{user_id}/portrait-image")
async def post_portrait_image_generate(
    user_id: str = FPath(...),
    body: dict = Body(default_factory=dict),
    _auth=Depends(require_auth),
):
    from user_stats import get_user, set_portrait_image_updated_date

    try:
        uid = int(user_id)
    except ValueError:
        return {"ok": False, "error": "Некорректный user_id"}
    user_id_str = str(uid)
    if user_id_str in _portrait_image_generating:
        return {"ok": False, "error": "Генерация уже идёт"}
    u = await _run_sync(get_user, uid, "")
    portrait = (u.get("portrait") or "").strip()
    if not portrait:
        return {"ok": False, "error": "Сначала составьте текстовый портрет"}
    provider = None
    p = (body.get("provider") or "").strip().lower()
    try:
        from services.portrait_image import PROVIDERS

        if p in PROVIDERS:
            provider = p
    except Exception:
        pass
    _portrait_image_generating.add(user_id_str)
    try:
        from services.portrait_image import generate_portrait_image

        path = await _run_sync(
            generate_portrait_image,
            uid,
            portrait,
            u.get("display_name", ""),
            provider=provider,
        )
        if path:
            await _run_sync(set_portrait_image_updated_date, uid)
            return {"ok": True, "message": "Портрет сгенерирован"}
        return {
            "ok": False,
            "error": "Не удалось сгенерировать. Проверьте баланс OpenRouter и логи сервера.",
        }
    except Exception as e:
        logger.exception("Ошибка генерации портрета: %s", e)
        return {"ok": False, "error": str(e)}
    finally:
        _portrait_image_generating.discard(user_id_str)


@router.post("/portrait-from-storage")
async def post_portrait_from_storage(body: dict = Body(default_factory=dict), _auth=Depends(require_auth)):
    from ai_analyzer import build_deep_portrait_from_messages
    from user_stats import get_user, get_user_messages_archive, set_deep_portrait

    user_id = body.get("user_id")
    chat_id = body.get("chat_id")
    if not user_id:
        return {"ok": False, "error": "Нужен user_id"}
    try:
        user_id_int = int(user_id)
    except ValueError:
        return {"ok": False, "error": "Некорректный user_id"}
    user_id_str = str(user_id_int)
    if user_id_str in _portrait_building:
        return {"ok": False, "error": "Портрет уже создаётся для этого пользователя"}
    _portrait_building.add(user_id_str)
    try:
        chat_id_int = int(chat_id) if chat_id and chat_id != "all" else None
        messages = await _run_sync(get_user_messages_archive, user_id_int, chat_id_int)
        if not messages:
            return {
                "ok": False,
                "error": "Нет сообщений в архиве. Бот накапливает сообщения по мере чтения чата. Подождите, пока участник напишет больше.",
            }
        u = await _run_sync(get_user, user_id_int)
        display_name = u.get("display_name", user_id)
        portrait, rank = await _run_sync(
            build_deep_portrait_from_messages, messages, display_name
        )
        await _run_sync(set_deep_portrait, user_id_int, portrait, rank)
        return {
            "ok": True,
            "messages_count": len(messages),
            "portrait_preview": (portrait[:500] + "…") if len(portrait) > 500 else portrait,
        }
    finally:
        _portrait_building.discard(user_id_str)


@router.post("/portrait-classify-unknown")
async def post_portrait_classify_unknown(body: dict = Body(default_factory=dict), _auth=Depends(require_auth)):
    from ai_analyzer import build_deep_portrait_from_messages
    from user_stats import get_user, get_user_messages_archive, get_users_in_chat, set_deep_portrait

    raw_chat = str(body.get("chat_id", "all") or "all").strip()
    chat_id: int | None = None
    if raw_chat != "all":
        if not raw_chat.lstrip("-").isdigit():
            return {"ok": False, "error": "Некорректный chat_id"}
        chat_id = int(raw_chat)
    users = (_load_users() or {}).get("users", {}) or {}
    chat_members: set[str] | None = None
    if chat_id is not None:
        try:
            chat_members = {
                str(int(uid)) for uid in (await _run_sync(get_users_in_chat, int(chat_id)) or [])
            }
        except Exception:
            chat_members = set()
    candidate_ids: list[int] = []
    for uid, row in users.items():
        uid_str = str(uid or "").strip()
        if not uid_str.lstrip("-").isdigit():
            continue
        if chat_members is not None and uid_str not in chat_members:
            continue
        rank = str((row or {}).get("rank", "unknown") or "unknown").strip().lower()
        if rank == "unknown":
            candidate_ids.append(int(uid_str))
    processed = 0
    skipped_no_messages = 0
    skipped_in_progress = 0
    failed = 0
    for uid in candidate_ids:
        uid_key = str(uid)
        if uid_key in _portrait_building:
            skipped_in_progress += 1
            continue
        _portrait_building.add(uid_key)
        try:
            messages = await _run_sync(get_user_messages_archive, uid, chat_id)
            if not messages:
                skipped_no_messages += 1
                continue
            u = await _run_sync(get_user, uid) or {}
            display_name = str(u.get("display_name") or uid)
            portrait, rank = await _run_sync(
                build_deep_portrait_from_messages, messages, display_name
            )
            await _run_sync(set_deep_portrait, uid, portrait, rank)
            processed += 1
        except Exception:
            failed += 1
        finally:
            _portrait_building.discard(uid_key)
    return {
        "ok": True,
        "chat_id": "all" if chat_id is None else int(chat_id),
        "unknown_total": len(candidate_ids),
        "processed": int(processed),
        "skipped_no_messages": int(skipped_no_messages),
        "skipped_in_progress": int(skipped_in_progress),
        "failed": int(failed),
    }


@router.get("/portrait-building-status")
async def get_portrait_building_status(
    user_id: str | None = Query(default=None),
    _auth=Depends(require_auth),
):
    if user_id:
        return {"building": user_id in _portrait_building}
    return {"building_user_ids": list(_portrait_building)}
