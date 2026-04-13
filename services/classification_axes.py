"""
Конфигурируемые оси классификации пользователей (ИИ по инструкции и списку категорий).
Файл: data/classification_axes.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_AXES_PATH = Path(__file__).resolve().parent.parent / "data" / "classification_axes.json"
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def axes_path() -> Path:
    return _AXES_PATH


def default_config() -> dict[str, Any]:
    return {
        "axes": [
            {
                "id": "political",
                "title": "Политическая позиция",
                "instruction": "По сообщениям пользователя определи политическую позицию.",
                "categories": "loyal\nneutral\nopposition\nunknown",
                "sync_with_rank": True,
                "enabled": True,
            }
        ]
    }


def load_axes_config() -> dict[str, Any]:
    if not _AXES_PATH.is_file():
        return default_config()
    try:
        raw = json.loads(_AXES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default_config()
    if not isinstance(raw, dict):
        return default_config()
    axes = raw.get("axes")
    if not isinstance(axes, list):
        raw["axes"] = []
    return raw


def save_axes_config(data: dict[str, Any]) -> None:
    _AXES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    _AXES_PATH.write_text(payload + "\n", encoding="utf-8")


def parse_categories(categories_str: str) -> list[str]:
    out: list[str] = []
    for line in (categories_str or "").replace(",", "\n").split("\n"):
        s = line.strip()
        if s:
            out.append(s)
    return out


def validate_axis(axis: dict[str, Any]) -> str | None:
    aid = str(axis.get("id") or "").strip().lower()
    if not _ID_RE.match(aid):
        return "id оси: только a-z, цифры, _, с 1–64 символов, начинаться с буквы"
    title = str(axis.get("title") or "").strip()
    if not title or len(title) > 200:
        return "title обязателен (до 200 символов)"
    instr = str(axis.get("instruction") or "").strip()
    if len(instr) < 10:
        return "instruction — минимум 10 символов (что именно классифицировать)"
    cats = parse_categories(str(axis.get("categories") or ""))
    if len(cats) < 2:
        return "categories — минимум 2 метки (по одной на строку или через запятую)"
    if len(cats) > 40:
        return "не больше 40 категорий на ось"
    return None


def validate_axes_payload(data: dict[str, Any]) -> str | None:
    if not isinstance(data, dict):
        return "неверное тело запроса"
    axes = data.get("axes")
    if not isinstance(axes, list):
        return "нужен массив axes"
    seen: set[str] = set()
    for ax in axes:
        if not isinstance(ax, dict):
            return "каждая ось должна быть объектом"
        err = validate_axis(ax)
        if err:
            return err
        aid = str(ax.get("id") or "").strip().lower()
        if aid in seen:
            return f"дубликат id: {aid}"
        seen.add(aid)
        ax["id"] = aid
        ax["title"] = str(ax.get("title") or "").strip()[:200]
        ax["instruction"] = str(ax.get("instruction") or "").strip()[:12000]
        ax["categories"] = str(ax.get("categories") or "").strip()[:8000]
        ax["sync_with_rank"] = bool(ax.get("sync_with_rank"))
        ax["enabled"] = bool(ax.get("enabled", True))
    return None


def get_axis_by_id(axis_id: str) -> dict[str, Any] | None:
    aid = str(axis_id or "").strip().lower()
    for ax in load_axes_config().get("axes") or []:
        if isinstance(ax, dict) and str(ax.get("id") or "").strip().lower() == aid:
            return ax
    return None


def enabled_axes() -> list[dict[str, Any]]:
    return [
        ax
        for ax in (load_axes_config().get("axes") or [])
        if isinstance(ax, dict) and ax.get("enabled", True)
    ]


def user_needs_axis_run(u: dict | None, axis_id: str, axis: dict[str, Any] | None = None) -> bool:
    """Нужно ли пересчитать ось при only_unknown: нет записи или метка unknown.

    Поле rank для political не считаем «уже классифицированным» без записи в
    classifications[political] — иначе ИИ никогда не запускался бы для neutral/opposition.

    ``axis`` kept for forward-compat but not used currently.
    """
    if not isinstance(u, dict):
        return True
    aid = str(axis_id or "").strip().lower()
    val = ""
    clf = u.get("classifications") or {}
    if isinstance(clf, dict) and aid in clf and isinstance(clf[aid], dict):
        val = str(clf[aid].get("value") or "").strip().lower()
    if val and val != "unknown":
        return False
    return True
