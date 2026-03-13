from __future__ import annotations

import json
import logging
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from uuid import uuid4

from services import learning_loop
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
) -> str:
    event_id = uuid4().hex[:16]
    event = {
        "event_id": event_id,
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
    return event_id


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

        strategy_scores = {
            "standard": 1.0,
            "gentle": 0.0,
            "careful": 0.0,
            "motivating": 0.0,
            "strict": 0.0,
        }
        action_hint = "standard_warning"
        level_delta = 0
        reasons: list[str] = []

        # Learning layer: deterministic A/B assignment + feedback-derived strategy bias.
        bias_ctx = learning_loop.compose_bias(int(chat_id), int(user_id))
        merged_bias = bias_ctx.get("bias") or {}
        for strategy_name, boost in merged_bias.items():
            if strategy_name in strategy_scores:
                strategy_scores[strategy_name] += float(boost or 0.0)
        variant = str(bias_ctx.get("variant") or "control")
        if variant and variant != "control":
            reasons.append(f"ab_variant:{variant}")

        # 1) Keep valuable influential users safer when chat is fragile.
        if health_score < 0.5 and influence > 0.7:
            strategy_scores["careful"] += 4.0
            strategy_scores["gentle"] += 1.0
            reasons.append("low_chat_health_high_influence")

        # 2) At-risk churn users should receive softer moderation.
        if churn_risk > 0.7:
            strategy_scores["motivating"] += 4.0
            strategy_scores["gentle"] += 1.0
            strategy_scores["strict"] -= 2.0
            reasons.append("high_churn_risk")

        # 3) Low engagement neutral users also need gentle approach.
        if str(sentiment or "").lower() == "neutral" and engagement < 0.3:
            strategy_scores["gentle"] += 3.0
            strategy_scores["motivating"] += 1.0
            reasons.append("low_engagement_neutral")

        # 4) In beast mode for low-risk users we can be stricter.
        if (
            str(style or "").lower() == "beast"
            and is_political
            and churn_risk < 0.4
            and influence < 0.5
        ):
            strategy_scores["strict"] += 4.0
            reasons.append("beast_mode_low_churn_low_influence")

        # Keep tie-breaking deterministic and conservative (standard first).
        ordered = ["standard", "gentle", "careful", "motivating", "strict"]
        strategy = max(
            ordered,
            key=lambda name: (
                float(strategy_scores.get(name, 0.0) or 0.0),
                -ordered.index(name),
            ),
        )
        if strategy == "strict":
            action_hint = "strict_warning"
            level_delta = 1
        elif strategy == "motivating":
            action_hint = "motivating_warning"
            level_delta = -1
        elif strategy in {"gentle", "careful"}:
            action_hint = "gentle_warning"
            level_delta = -1

        if not reasons:
            reasons.append("default_policy")

        metrics = {
            "engagement": round(engagement, 4),
            "influence": round(influence, 4),
            "churn_risk": round(churn_risk, 4),
            "health_score": round(health_score, 4),
            "political_count": int(political_count),
            "ab_variant": variant,
            "ab_variant_enabled": bool(bias_ctx.get("variant_enabled", True)),
            "strategy_scores": {k: round(float(v), 4) for k, v in strategy_scores.items()},
            "ab_bias": {str(k): round(float(v), 4) for k, v in (merged_bias or {}).items()},
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


def _feedback_label_to_score(label: str) -> float:
    raw = str(label or "").strip().lower()
    if raw in {"approve", "accepted", "good", "positive"}:
        return 1.0
    if raw in {"reject", "rejected", "bad", "negative"}:
        return -1.0
    return 0.0


def apply_decision_feedback(
    *,
    event_id: str,
    feedback: str,
    score: float | None = None,
    reviewer: str = "admin",
    note: str = "",
) -> dict | None:
    event_id_norm = str(event_id or "").strip()
    if not event_id_norm:
        return None
    score_norm = float(score) if score is not None else _feedback_label_to_score(feedback)
    updated: dict | None = None
    with _LOCK:
        data = _load()
        rows = data.setdefault("events", [])
        for row in reversed(rows):
            if str(row.get("event_id") or "") != event_id_norm:
                continue
            row["feedback_label"] = str(feedback or "neutral").strip().lower()
            row["feedback_score"] = max(-1.0, min(1.0, float(score_norm)))
            row["feedback_reviewer"] = str(reviewer or "admin")[:80]
            row["feedback_note"] = str(note or "")[:280]
            row["feedback_ts"] = time.time()
            updated = row
            break
        if updated is not None:
            _save(data)
    if updated is None:
        return None
    try:
        learning_loop.append_feedback(
            event_id=str(updated.get("event_id") or ""),
            chat_id=int(updated.get("chat_id", 0) or 0),
            user_id=int(updated.get("user_id")) if updated.get("user_id") is not None else None,
            strategy=str(updated.get("strategy") or "standard"),
            variant=str((updated.get("metrics") or {}).get("ab_variant") or "control"),
            score=float(updated.get("feedback_score", 0.0) or 0.0),
            label=str(updated.get("feedback_label") or "neutral"),
            note=str(updated.get("feedback_note") or ""),
        )
    except Exception as e:
        logger.debug("learning feedback append failed: %s", e)
    return updated


def get_decision_quality(*, chat_id: int | None = None, days: int = 30) -> dict:
    now_ts = time.time()
    window_sec = max(1, int(days or 30)) * 24 * 3600
    with _LOCK:
        rows = list(reversed((_load().get("events") or [])))
    selected: list[dict] = []
    for row in rows:
        if chat_id is not None and int(row.get("chat_id", 0) or 0) != int(chat_id):
            continue
        if float(row.get("ts", 0.0) or 0.0) < (now_ts - window_sec):
            continue
        selected.append(row)
    with_feedback = [x for x in selected if "feedback_score" in x]
    approval = len([x for x in with_feedback if float(x.get("feedback_score", 0.0) or 0.0) > 0.0])
    rejection = len([x for x in with_feedback if float(x.get("feedback_score", 0.0) or 0.0) < 0.0])
    neutral = len(with_feedback) - approval - rejection
    avg_score = sum(float(x.get("feedback_score", 0.0) or 0.0) for x in with_feedback) / max(1, len(with_feedback))
    by_strategy: dict[str, dict] = {}
    for row in with_feedback:
        key = str(row.get("strategy") or "unknown")
        agg = by_strategy.setdefault(key, {"strategy": key, "count": 0, "avg_score": 0.0})
        agg["count"] += 1
        agg["avg_score"] += float(row.get("feedback_score", 0.0) or 0.0)
    strategy_rows = []
    for agg in by_strategy.values():
        strategy_rows.append(
            {
                "strategy": agg["strategy"],
                "count": int(agg["count"]),
                "avg_score": round(float(agg["avg_score"]) / max(1, int(agg["count"])), 4),
            }
        )
    strategy_rows.sort(key=lambda x: (x["count"], x["avg_score"]), reverse=True)
    learning = learning_loop.feedback_summary(chat_id=chat_id, days=days)
    return {
        "chat_id": "all" if chat_id is None else int(chat_id),
        "days": int(max(1, int(days or 30))),
        "total_decisions": len(selected),
        "feedback_count": len(with_feedback),
        "approval_rate": round(float(approval) / max(1, len(with_feedback)), 4),
        "avg_feedback_score": round(float(avg_score), 4),
        "approved": int(approval),
        "rejected": int(rejection),
        "neutral": int(neutral),
        "by_strategy": strategy_rows,
        "learning_summary": learning,
    }
