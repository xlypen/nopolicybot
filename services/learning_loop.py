from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

_PATH = Path(__file__).resolve().parent.parent / "data" / "learning_feedback.json"
_LOCK = Lock()

_DEFAULT_VARIANT_WEIGHTS = {
    "control": 1.0,
    "retention_bias": 1.0,
    "stability_bias": 1.0,
    "strict_guard": 1.0,
}

_VARIANT_BIAS = {
    "control": {},
    "retention_bias": {"motivating": 0.35, "gentle": 0.15, "strict": -0.2},
    "stability_bias": {"careful": 0.3, "gentle": 0.1, "strict": -0.1},
    "strict_guard": {"strict": 0.25, "motivating": -0.1, "gentle": -0.05},
}


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_payload() -> dict:
    return {
        "version": 1,
        "updated_at": _utc_now().isoformat(),
        "experiments": {
            "strategy_ab": {
                "enabled": True,
                "weights": dict(_DEFAULT_VARIANT_WEIGHTS),
            }
        },
        "feedback": [],
    }


def _load() -> dict:
    if not _PATH.exists():
        return _new_payload()
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _new_payload()
        data.setdefault("version", 1)
        data.setdefault("updated_at", _utc_now().isoformat())
        data.setdefault("experiments", {})
        data.setdefault("feedback", [])
        return data
    except Exception:
        return _new_payload()


def _save(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=_PATH.parent) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    tmp_path.replace(_PATH)


def _score_window(rows: list[dict], *, days: int = 30, chat_id: int | None = None) -> list[dict]:
    since = _utc_now() - timedelta(days=max(1, int(days or 30)))
    out: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if chat_id is not None and int(row.get("chat_id", 0) or 0) != int(chat_id):
            continue
        ts_raw = str(row.get("ts") or "")
        try:
            ts = datetime.fromisoformat(ts_raw) if ts_raw else None
        except Exception:
            ts = None
        if ts is None or ts < since:
            continue
        out.append(row)
    return out


def _variant_weights(data: dict) -> dict[str, float]:
    exp = ((data.get("experiments") or {}).get("strategy_ab") or {})
    raw = exp.get("weights") or {}
    out = {}
    for key, default_weight in _DEFAULT_VARIANT_WEIGHTS.items():
        try:
            out[key] = max(0.0, float(raw.get(key, default_weight)))
        except Exception:
            out[key] = float(default_weight)
    if not any(v > 0 for v in out.values()):
        return dict(_DEFAULT_VARIANT_WEIGHTS)
    return out


def select_variant(chat_id: int, user_id: int) -> dict:
    with _LOCK:
        data = _load()
    exp = ((data.get("experiments") or {}).get("strategy_ab") or {})
    enabled = bool(exp.get("enabled", True))
    if not enabled:
        return {"enabled": False, "variant": "control", "bias": {}}

    weights = _variant_weights(data)
    variants = [k for k, w in weights.items() if float(w) > 0.0]
    if not variants:
        variants = ["control"]
    total = sum(float(weights.get(v, 0.0) or 0.0) for v in variants)
    key = f"{int(chat_id)}:{int(user_id)}:strategy_ab"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()
    bucket = (int(h[:12], 16) / float(16**12)) * float(total or 1.0)
    cursor = 0.0
    selected = variants[0]
    for variant in variants:
        cursor += float(weights.get(variant, 0.0) or 0.0)
        if bucket <= cursor:
            selected = variant
            break
    return {
        "enabled": True,
        "variant": selected,
        "bias": dict(_VARIANT_BIAS.get(selected) or {}),
    }


def learned_strategy_bias(*, chat_id: int | None = None, days: int = 30) -> dict[str, float]:
    with _LOCK:
        data = _load()
    rows = _score_window(data.get("feedback") or [], days=days, chat_id=chat_id)
    by_strategy: dict[str, list[float]] = {}
    for row in rows:
        strategy = str(row.get("strategy") or "").strip().lower()
        if not strategy:
            continue
        try:
            score = float(row.get("score", 0.0) or 0.0)
        except Exception:
            score = 0.0
        by_strategy.setdefault(strategy, []).append(score)
    out: dict[str, float] = {}
    for strategy, values in by_strategy.items():
        if len(values) < 5:
            continue
        avg = sum(values) / max(1, len(values))
        # Constrain adaptation strength to keep rule base dominant.
        out[strategy] = max(-0.4, min(0.4, avg * 0.4))
    return out


def compose_bias(chat_id: int, user_id: int) -> dict:
    variant_ctx = select_variant(chat_id, user_id)
    variant_bias = dict(variant_ctx.get("bias") or {})
    learned_bias = learned_strategy_bias(chat_id=chat_id, days=30)
    merged: dict[str, float] = {}
    for key, value in variant_bias.items():
        merged[str(key)] = float(value or 0.0)
    for key, value in learned_bias.items():
        merged[str(key)] = float(merged.get(str(key), 0.0) + float(value or 0.0))
    return {
        "variant": str(variant_ctx.get("variant") or "control"),
        "variant_enabled": bool(variant_ctx.get("enabled", True)),
        "variant_bias": variant_bias,
        "learned_bias": learned_bias,
        "bias": merged,
    }


def append_feedback(
    *,
    event_id: str,
    chat_id: int,
    user_id: int | None,
    strategy: str,
    variant: str,
    score: float,
    label: str,
    note: str = "",
) -> dict:
    row = {
        "ts": _utc_now().isoformat(),
        "event_id": str(event_id or ""),
        "chat_id": int(chat_id),
        "user_id": int(user_id) if user_id is not None else None,
        "strategy": str(strategy or "standard"),
        "variant": str(variant or "control"),
        "score": float(max(-1.0, min(1.0, float(score or 0.0)))),
        "label": str(label or "neutral"),
        "note": str(note or "")[:280],
    }
    with _LOCK:
        data = _load()
        rows = data.setdefault("feedback", [])
        rows.append(row)
        data["feedback"] = rows[-2500:]
        data["updated_at"] = _utc_now().isoformat()
        _save(data)
    return row


def feedback_summary(*, chat_id: int | None = None, days: int = 30) -> dict:
    with _LOCK:
        data = _load()
    rows = _score_window(data.get("feedback") or [], days=days, chat_id=chat_id)
    by_strategy: dict[str, list[dict]] = {}
    by_variant: dict[str, list[dict]] = {}
    for row in rows:
        by_strategy.setdefault(str(row.get("strategy") or "unknown"), []).append(row)
        by_variant.setdefault(str(row.get("variant") or "control"), []).append(row)

    def _agg(groups: dict[str, list[dict]]) -> list[dict]:
        out: list[dict] = []
        for key, items in groups.items():
            scores = [float(x.get("score", 0.0) or 0.0) for x in items]
            approved = len([x for x in items if str(x.get("label") or "").lower() == "approve"])
            rejected = len([x for x in items if str(x.get("label") or "").lower() == "reject"])
            neutral = len(items) - approved - rejected
            avg = sum(scores) / max(1, len(scores))
            out.append(
                {
                    "name": key,
                    "events": len(items),
                    "avg_score": round(avg, 4),
                    "approved": int(approved),
                    "rejected": int(rejected),
                    "neutral": int(neutral),
                    "approval_rate": round(float(approved) / max(1, len(items)), 4),
                }
            )
        out.sort(key=lambda x: (x["events"], x["avg_score"]), reverse=True)
        return out

    return {
        "days": int(max(1, int(days or 30))),
        "chat_id": "all" if chat_id is None else int(chat_id),
        "total_events": len(rows),
        "strategy": _agg(by_strategy),
        "variant": _agg(by_variant),
        "active_variant_weights": _variant_weights(data),
    }
