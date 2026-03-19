from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Callable

import bot_settings

_POLICIES_PATH = Path(__file__).resolve().parent.parent / "data" / "topic_policies.json"

# Extensible topic catalog. "politics" stays default for backward compatibility.
DEFAULT_TOPIC_POLICIES: dict[str, dict] = {
    "politics": {
        "enabled": True,
        "action": "moderate",
        "priority": 100,
        "label": "Политика",
        "description": "Текущий основной модерационный паттерн.",
        "keywords": [],
    },
    "hate_speech": {
        "enabled": False,
        "action": "moderate",
        "priority": 90,
        "label": "Токсичность и оскорбления",
        "description": "Потенциально токсичные формулировки.",
        "keywords": ["ненавиж", "ублюд", "мраз", "твар", "идиот", "дебил"],
    },
    "finance_spam": {
        "enabled": False,
        "action": "observe",
        "priority": 60,
        "label": "Финансовый спам",
        "description": "Сигналы спам/скам-предложений.",
        "keywords": ["инвестируй", "x100", "сигналы", "гарант доход", "быстрый заработок"],
    },
}


def _load_custom() -> dict:
    if not _POLICIES_PATH.exists():
        return {"policies": {}}
    try:
        payload = json.loads(_POLICIES_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("policies"), dict):
            return payload
    except Exception:
        pass
    return {"policies": {}}


def _save_custom(payload: dict) -> None:
    _POLICIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=_POLICIES_PATH.parent) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    tmp_path.replace(_POLICIES_PATH)


def _normalize_policy(name: str, value: dict | None) -> dict:
    base = dict(DEFAULT_TOPIC_POLICIES.get(name, {}))
    incoming = dict(value or {})
    merged = {**base, **incoming}
    merged["enabled"] = bool(merged.get("enabled", True))
    merged["action"] = str(merged.get("action", "observe")).strip().lower()
    if merged["action"] not in {"moderate", "observe"}:
        merged["action"] = "observe"
    try:
        merged["priority"] = int(merged.get("priority", 50))
    except Exception:
        merged["priority"] = 50
    merged["label"] = str(merged.get("label", name)).strip() or name
    merged["description"] = str(merged.get("description", "") or "")
    kws = merged.get("keywords")
    if not isinstance(kws, list):
        kws = []
    merged["keywords"] = [str(x).strip().lower() for x in kws if str(x).strip()]
    return merged


def get_topic_policies() -> dict[str, dict]:
    custom = _load_custom().get("policies") or {}
    names = set(DEFAULT_TOPIC_POLICIES.keys()) | set(custom.keys())
    out: dict[str, dict] = {}
    for name in sorted(names):
        out[name] = _normalize_policy(name, custom.get(name))
    return out


def set_topic_policy(name: str, patch: dict) -> dict[str, dict]:
    key = str(name or "").strip().lower()
    if not key:
        raise ValueError("topic name is required")
    payload = _load_custom()
    policies = payload.setdefault("policies", {})
    current = _normalize_policy(key, policies.get(key))
    policies[key] = {**current, **dict(patch or {})}
    payload["policies"] = policies
    _save_custom(payload)
    return get_topic_policies()


def reset_topic_policies(names: list[str] | None = None) -> dict[str, dict]:
    if names is None:
        _save_custom({"policies": {}})
        return get_topic_policies()
    payload = _load_custom()
    policies = payload.setdefault("policies", {})
    for raw in names:
        key = str(raw or "").strip().lower()
        if key in policies:
            del policies[key]
    payload["policies"] = policies
    _save_custom(payload)
    return get_topic_policies()


def get_primary_topic(chat_id: int | None = None) -> str:
    raw = str(bot_settings.get("primary_moderation_topic", chat_id) or "politics").strip().lower()
    return raw or "politics"


def set_primary_topic(topic: str) -> str:
    val = str(topic or "").strip().lower()
    if not val:
        val = "politics"
    bot_settings.set_all({"primary_moderation_topic": val})
    return val


def get_topic_label(topic: str) -> str:
    policies = get_topic_policies()
    key = str(topic or "").strip().lower()
    p = policies.get(key) or {}
    return str(p.get("label") or key or "тема")


def resolve_topic_trigger(
    text: str,
    *,
    special_matchers: dict[str, Callable[[str], bool]] | None = None,
) -> dict:
    source = (text or "").strip()
    if not source:
        return {"matched_topics": [], "moderation_topics": [], "trigger_topic": None}
    lower = source.lower()
    policies = get_topic_policies()
    matched: list[str] = []
    for topic, policy in policies.items():
        if not bool(policy.get("enabled", True)):
            continue
        matcher = (special_matchers or {}).get(topic)
        if matcher is not None:
            try:
                if bool(matcher(source)):
                    matched.append(topic)
            except Exception:
                pass
            continue
        keywords = policy.get("keywords") or []
        if any(kw in lower for kw in keywords):
            matched.append(topic)
    moderation_topics = [
        topic
        for topic in matched
        if str((policies.get(topic) or {}).get("action", "observe")).lower() == "moderate"
    ]
    moderation_topics.sort(key=lambda topic: -int((policies.get(topic) or {}).get("priority", 50)))
    trigger = moderation_topics[0] if moderation_topics else None
    return {
        "matched_topics": matched,
        "moderation_topics": moderation_topics,
        "trigger_topic": trigger,
    }
