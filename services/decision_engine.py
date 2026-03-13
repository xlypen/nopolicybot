from __future__ import annotations

import json
import logging
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock

from services.marketing_metrics import get_chat_health, get_user_metrics

logger = logging.getLogger(__name__)

_DECISIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "decision_events.json"
_LOCK = Lock()
_MAX_EVENTS = 1200


@dataclass
class DecisionResult:
    strategy: str
    action_hint: str
    level_delta: int
    reasons: list[str]
    metrics: dict


def _load() -> dict:
    if not _DECISIONS_PATH.exists():
        return {"events": []}
    try:
        data = json.loads(_DECISIONS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            return data
    except Exception:
        pass
    return {"events": []}


def _save(data: dict) -> None:
    _DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=_DECISIONS_PATH.parent) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    tmp_path.replace(_DECISIONS_PATH)


def append_decision_event(
    *,
    chat_id: int,
    user_id: int | None,
    sentiment: str,
    is_political: bool,
    style: str,
    political_count: int | None,
    result: DecisionResult,
    outcome: str,
    detail: str = "",
) -> None:
    event = {
        "ts": time.time(),
        "chat_id": int(chat_id),
        "user_id": int(user_id) if user_id is not None else None,
        "sentiment": str(sentiment or ""),
        "is_political": bool(is_political),
        "style": str(style or ""),
        "political_count": int(political_count) if political_count is not None else None,
        "strategy": result.strategy,
        "action_hint": result.action_hint,
        "level_delta": int(result.level_delta),
        "reasons": list(result.reasons),
        "metrics": result.metrics,
        "outcome": str(outcome or "")[:80],
        "detail": str(detail or "")[:400],
    }
    with _LOCK:
        data = _load()
        rows = data.setdefault("events", [])
        rows.append(event)
        data["events"] = rows[-_MAX_EVENTS:]
        _save(data)


def get_recent_decisions(limit: int = 80, chat_id: int | None = None, user_id: int | None = None) -> list[dict]:
    with _LOCK:
        rows = list(reversed((_load().get("events") or [])))
    out: list[dict] = []
    for row in rows:
        if chat_id is not None and int(row.get("chat_id") or 0) != int(chat_id):
            continue
        if user_id is not None and int(row.get("user_id") or 0) != int(user_id):
            continue
        out.append(row)
        if len(out) >= max(1, int(limit)):
            break
    return out


class DecisionEngine:
    """
    Rule-based strategy selection over user/chat metrics.
    """

    def decide(
        self,
        *,
        chat_id: int,
        user_id: int,
        sentiment: str,
        is_political: bool,
        style: str,
        political_count: int,
    ) -> DecisionResult:
        user = get_user_metrics(user_id, chat_id=chat_id, days=30)
        health = get_chat_health(chat_id, days=30)

        engagement = float(user.get("engagement_score", 0.0) or 0.0)
        influence = float(user.get("influence_score", 0.0) or 0.0)
        churn_risk = float(user.get("churn_risk", 1.0) or 1.0)
        health_score = float(health.get("health_score", 0.0) or 0.0)

        strategy = "standard"
        action_hint = "standard_warning"
        level_delta = 0
        reasons: list[str] = []

        # 1) Keep valuable influential users safer when chat is fragile.
        if health_score < 0.5 and influence > 0.7:
            strategy = "careful"
            action_hint = "gentle_warning"
            level_delta = min(level_delta, -1)
            reasons.append("low_chat_health_high_influence")

        # 2) At-risk churn users should receive softer moderation.
        if churn_risk > 0.7:
            strategy = "motivating"
            action_hint = "motivating_warning"
            level_delta = min(level_delta, -1)
            reasons.append("high_churn_risk")

        # 3) Low engagement neutral users also need gentle approach.
        if str(sentiment or "").lower() == "neutral" and engagement < 0.3:
            if strategy == "standard":
                strategy = "gentle"
                action_hint = "gentle_warning"
            level_delta = min(level_delta, -1)
            reasons.append("low_engagement_neutral")

        # 4) In beast mode for low-risk users we can be stricter.
        if (
            str(style or "").lower() == "beast"
            and is_political
            and churn_risk < 0.4
            and influence < 0.5
            and strategy == "standard"
        ):
            strategy = "strict"
            action_hint = "strict_warning"
            level_delta = max(level_delta, 1)
            reasons.append("beast_mode_low_churn_low_influence")

        if not reasons:
            reasons.append("default_policy")

        metrics = {
            "engagement": round(engagement, 4),
            "influence": round(influence, 4),
            "churn_risk": round(churn_risk, 4),
            "health_score": round(health_score, 4),
            "political_count": int(political_count),
        }
        return DecisionResult(
            strategy=strategy,
            action_hint=action_hint,
            level_delta=int(level_delta),
            reasons=reasons,
            metrics=metrics,
        )


def serialize_decision(result: DecisionResult) -> dict:
    return asdict(result)
