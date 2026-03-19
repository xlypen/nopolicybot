from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import user_stats
from services.marketing_metrics import get_chat_health, get_user_metrics

_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "churn_snapshots.json"
_OUTREACH_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "retention_actions.json"
_STATE_LOCK = Lock()


def _save_snapshot(payload: dict) -> None:
    _SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=_SNAPSHOT_PATH.parent) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    tmp_path.replace(_SNAPSHOT_PATH)


def _load_snapshot() -> dict:
    if not _SNAPSHOT_PATH.exists():
        return {"snapshots": []}
    try:
        payload = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("snapshots"), list):
            return payload
    except Exception:
        pass
    return {"snapshots": []}


def _load_outreach_state() -> dict:
    if not _OUTREACH_STATE_PATH.exists():
        return {"sent": {}}
    try:
        payload = json.loads(_OUTREACH_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("sent"), dict):
            return payload
    except Exception:
        pass
    return {"sent": {}}


def _save_outreach_state(payload: dict) -> None:
    _OUTREACH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=_OUTREACH_STATE_PATH.parent) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    tmp_path.replace(_OUTREACH_STATE_PATH)


def _resolve_users(chat_id: int | None) -> list[tuple[int, str]]:
    data = user_stats._load() if hasattr(user_stats, "_load") else {"users": {}}
    users = data.get("users", {}) or {}
    if chat_id is not None and hasattr(user_stats, "get_users_in_chat"):
        in_chat = set(str(x) for x in user_stats.get_users_in_chat(chat_id))
    else:
        in_chat = set(users.keys())
    rows: list[tuple[int, str]] = []
    for uid_raw, user in users.items():
        if uid_raw not in in_chat:
            continue
        try:
            uid = int(uid_raw)
        except Exception:
            continue
        rows.append((uid, str(user.get("display_name") or uid_raw)))
    return rows


def build_retention_dashboard(chat_id: int | None = None, *, days: int = 30, limit: int = 50) -> dict:
    users = _resolve_users(chat_id)
    rows: list[dict] = []
    for uid, display_name in users:
        metrics = get_user_metrics(uid, chat_id=chat_id, days=days)
        totals = metrics.get("totals") or {}
        components = metrics.get("components") or {}
        negative = float(totals.get("negative", 0.0) or 0.0)
        positive = float(totals.get("positive", 0.0) or 0.0)
        neutral = float(totals.get("neutral", 0.0) or 0.0)
        sentiment_total = max(1.0, negative + positive + neutral)
        rows.append(
            {
                "user_id": uid,
                "display_name": display_name,
                "retention_score": float(metrics.get("retention_score", 0.0) or 0.0),
                "churn_risk": float(metrics.get("churn_risk", 1.0) or 1.0),
                "engagement_score": float(metrics.get("engagement_score", 0.0) or 0.0),
                "influence_score": float(metrics.get("influence_score", 0.0) or 0.0),
                "viral_coefficient": float(metrics.get("viral_coefficient", 0.0) or 0.0),
                "active_days": int(metrics.get("active_days", 0) or 0),
                "activity_streak": int(metrics.get("activity_streak", 0) or 0),
                "negative_share": float(negative / sentiment_total),
                "reach_factor": float(components.get("reach_factor", 0.0) or 0.0),
                "discussion_depth": float(components.get("discussion_depth", 0.0) or 0.0),
            }
        )
    rows.sort(key=lambda row: row["churn_risk"], reverse=True)
    rows = rows[: max(1, min(int(limit or 50), 500))]

    at_risk = [row for row in rows if row["churn_risk"] >= 0.65]
    high_value_at_risk = [row for row in at_risk if row["influence_score"] >= 0.55]
    viral = sorted(rows, key=lambda row: row["viral_coefficient"], reverse=True)[:10]
    health = get_chat_health(chat_id, days=days) if chat_id is not None else None

    return {
        "chat_id": "all" if chat_id is None else int(chat_id),
        "days": int(days),
        "summary": {
            "users_total": len(users),
            "users_considered": len(rows),
            "at_risk_count": len(at_risk),
            "high_value_at_risk_count": len(high_value_at_risk),
            "viral_contributors_count": len([x for x in viral if x["viral_coefficient"] >= 0.5]),
        },
        "health": health,
        "at_risk": at_risk,
        "high_value_at_risk": high_value_at_risk,
        "viral_contributors": viral,
    }


def build_recommendations(chat_id: int | None = None, *, days: int = 30, limit: int = 20) -> dict:
    dashboard = build_retention_dashboard(chat_id, days=days, limit=max(limit, 50))
    items: list[dict] = []
    optimization: dict = {}

    health_score = float((dashboard.get("health") or {}).get("health_score", 0.0) or 0.0) if dashboard.get("health") else None
    if health_score is not None and health_score < 0.5:
        items.append(
            {
                "type": "chat_health_alert",
                "priority": "high",
                "title": "Низкое здоровье чата",
                "reason": f"health_score={health_score:.2f}",
                "action": "Снизить жесткость модерации и усилить позитивные интервенции на 24ч.",
            }
        )

    for row in dashboard.get("high_value_at_risk", [])[:5]:
        items.append(
            {
                "type": "retention_high_value",
                "priority": "high",
                "user_id": row["user_id"],
                "display_name": row["display_name"],
                "reason": f"churn={row['churn_risk']:.2f}, influence={row['influence_score']:.2f}",
                "action": "Персонально вовлечь: мягкий контакт, упоминание и вопрос по теме пользователя.",
            }
        )

    for row in dashboard.get("at_risk", [])[:5]:
        if any(x.get("user_id") == row["user_id"] for x in items):
            continue
        items.append(
            {
                "type": "retention_standard",
                "priority": "medium",
                "user_id": row["user_id"],
                "display_name": row["display_name"],
                "reason": f"churn={row['churn_risk']:.2f}, retention={row['retention_score']:.2f}",
                "action": "Проверить вовлеченность и дать точку входа в обсуждение.",
            }
        )

    for row in dashboard.get("viral_contributors", [])[:3]:
        if row["viral_coefficient"] < 0.6:
            continue
        items.append(
            {
                "type": "viral_support",
                "priority": "medium",
                "user_id": row["user_id"],
                "display_name": row["display_name"],
                "reason": f"viral={row['viral_coefficient']:.2f}",
                "action": "Подсветить контент/роль пользователя, чтобы закрепить рост.",
            }
        )

    # Phase 5: predictive and learning-aware optimization hints.
    try:
        from services.decision_engine import get_decision_quality
        from services.predictive_models import predict_overview

        pred = predict_overview(chat_id, horizon_days=7, lookback_days=max(14, int(days)))
        quality = get_decision_quality(chat_id=chat_id, days=30)
        optimization = {"predictive": pred, "decision_quality": quality}

        churn_sig = ((pred.get("signals") or {}).get("churn_risk") or {})
        tox_sig = ((pred.get("signals") or {}).get("toxicity") or {})
        vir_sig = ((pred.get("signals") or {}).get("virality") or {})
        if str(churn_sig.get("direction") or "") == "up" and float(churn_sig.get("predicted", 0.0) or 0.0) >= 0.45:
            items.append(
                {
                    "type": "predictive_churn_risk",
                    "priority": "high",
                    "reason": f"churn forecast up to {float(churn_sig.get('predicted', 0.0) or 0.0):.2f}",
                    "action": "Запустить retention-campaign: targeted DM и мягкие входные вопросы в активные треды.",
                }
            )
        if str(tox_sig.get("direction") or "") == "up" and float(tox_sig.get("predicted", 0.0) or 0.0) >= 0.35:
            items.append(
                {
                    "type": "predictive_toxicity_risk",
                    "priority": "high",
                    "reason": f"toxicity forecast up to {float(tox_sig.get('predicted', 0.0) or 0.0):.2f}",
                    "action": "Снизить строгость авто-реакций, добавить de-escalation шаблоны и ручной контроль конфликтных пар.",
                }
            )
        if str(vir_sig.get("direction") or "") == "up" and float(vir_sig.get("predicted", 0.0) or 0.0) >= 0.55:
            items.append(
                {
                    "type": "predictive_virality_window",
                    "priority": "medium",
                    "reason": f"virality forecast up to {float(vir_sig.get('predicted', 0.0) or 0.0):.2f}",
                    "action": "Поддержать рост: закрепить лидеров, pin лучших реплик и организовать follow-up обсуждение.",
                }
            )
        feedback_count = int((quality or {}).get("feedback_count", 0) or 0)
        approval_rate = float((quality or {}).get("approval_rate", 0.0) or 0.0)
        if feedback_count >= 8 and approval_rate < 0.6:
            items.append(
                {
                    "type": "decision_quality_drop",
                    "priority": "high",
                    "reason": f"decision approval rate={approval_rate:.2f} across {feedback_count} feedback events",
                    "action": "Включить A/B review: повысить долю gentle/motivating стратегий и пересмотреть strict-policy thresholds.",
                }
            )
    except Exception:
        optimization = {"predictive": {}, "decision_quality": {}}

    items.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}.get(x.get("priority", "low"), 2))
    items = items[: max(1, min(int(limit or 20), 100))]
    return {
        "chat_id": "all" if chat_id is None else int(chat_id),
        "days": int(days),
        "items": items,
        "optimization": optimization,
    }


def run_churn_detection(chat_id: int | None = None, *, days: int = 30, limit: int = 200) -> dict:
    dashboard = build_retention_dashboard(chat_id, days=days, limit=limit)
    snapshot = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "chat_id": "all" if chat_id is None else int(chat_id),
        "days": int(days),
        "summary": dashboard.get("summary", {}),
        "at_risk": dashboard.get("at_risk", [])[:100],
        "high_value_at_risk": dashboard.get("high_value_at_risk", [])[:50],
        "viral_contributors": dashboard.get("viral_contributors", [])[:30],
    }
    payload = _load_snapshot()
    rows = payload.setdefault("snapshots", [])
    rows.append(snapshot)
    payload["snapshots"] = rows[-120:]
    _save_snapshot(payload)
    return snapshot


def get_recent_churn_snapshots(limit: int = 10, chat_id: int | None = None) -> list[dict]:
    payload = _load_snapshot()
    rows = list(reversed(payload.get("snapshots") or []))
    out: list[dict] = []
    for row in rows:
        rid = row.get("chat_id")
        if chat_id is not None and str(rid) != str(int(chat_id)):
            continue
        out.append(row)
        if len(out) >= max(1, int(limit)):
            break
    return out


def pick_at_risk_for_outreach(
    chat_id: int | None,
    *,
    days: int = 30,
    min_churn_risk: float = 0.72,
    limit: int = 5,
) -> list[dict]:
    dashboard = build_retention_dashboard(chat_id, days=days, limit=500)
    rows = [row for row in (dashboard.get("at_risk") or []) if float(row.get("churn_risk", 0.0) or 0.0) >= float(min_churn_risk)]
    # Prioritize users who are both at risk and influential.
    rows.sort(
        key=lambda row: (
            -float(row.get("influence_score", 0.0) or 0.0),
            -float(row.get("churn_risk", 0.0) or 0.0),
            float(row.get("engagement_score", 0.0) or 0.0),
        )
    )
    return rows[: max(1, min(int(limit or 5), 50))]


def should_send_retention_dm(user_id: int, chat_id: int, *, cooldown_hours: int = 24) -> bool:
    key = f"{int(chat_id)}:{int(user_id)}"
    now_ts = datetime.now(tz=timezone.utc).timestamp()
    cooldown_sec = max(1, int(cooldown_hours or 24)) * 3600
    with _STATE_LOCK:
        payload = _load_outreach_state()
        sent = payload.setdefault("sent", {})
        last_ts = float(sent.get(key, 0.0) or 0.0)
        return (now_ts - last_ts) >= cooldown_sec


def mark_retention_dm_sent(user_id: int, chat_id: int) -> None:
    key = f"{int(chat_id)}:{int(user_id)}"
    now_ts = datetime.now(tz=timezone.utc).timestamp()
    with _STATE_LOCK:
        payload = _load_outreach_state()
        sent = payload.setdefault("sent", {})
        sent[key] = now_ts
        # Keep state compact by dropping stale entries older than 30 days.
        min_keep_ts = now_ts - (30 * 24 * 3600)
        payload["sent"] = {k: v for k, v in sent.items() if float(v or 0.0) >= min_keep_ts}
        _save_outreach_state(payload)
