from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import re
from typing import Any

import user_stats
from services import marketing_metrics
from services.community_health import build_community_health
from services.decision_engine import get_decision_quality, get_recent_decisions
from services.learning_loop import feedback_summary
from services.moderation_risk import build_moderation_risk
from services.data_privacy import user_hash
from services.predictive_models import predict_overview
from services.recommendations import build_retention_dashboard, get_recent_churn_snapshots
from services.tone_analyzer import ToneAnalyzer

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]{2,}")
_STOP_WORDS = {
    "это",
    "как",
    "что",
    "так",
    "для",
    "или",
    "при",
    "если",
    "она",
    "они",
    "его",
    "ее",
    "вам",
    "нас",
    "про",
    "тут",
    "там",
    "быть",
    "есть",
    "нет",
    "очень",
    "просто",
    "когда",
    "чтобы",
    "почему",
    "где",
    "надо",
    "можно",
    "через",
    "после",
    "before",
    "about",
    "this",
    "that",
    "with",
    "from",
    "have",
    "will",
    "would",
    "could",
}


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _now_ts() -> float:
    return datetime.now(tz=timezone.utc).timestamp()


def _parse_day(raw: Any) -> date | None:
    text = str(raw or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except Exception:
        return None


def _trend_ratio(values: list[float], window: int = 7) -> float:
    if len(values) < max(4, window * 2):
        return 0.0
    recent = values[-window:]
    prev = values[-(window * 2) : -window]
    recent_avg = sum(recent) / max(1, len(recent))
    prev_avg = sum(prev) / max(1, len(prev))
    if prev_avg <= 0.0:
        return 0.0 if recent_avg <= 0.0 else 1.0
    return (recent_avg - prev_avg) / prev_avg


def _chat_ids_known() -> list[int]:
    ids: list[int] = []
    try:
        for row in user_stats.get_chats() or []:
            raw = row.get("chat_id")
            if str(raw).lstrip("-").isdigit():
                ids.append(int(raw))
    except Exception:
        return []
    out: list[int] = []
    seen: set[int] = set()
    for cid in ids:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    return out


def _collect_messages(chat_id: int | None, *, days: int = 30, limit: int = 3000) -> list[str]:
    data = user_stats._load() if hasattr(user_stats, "_load") else {"users": {}}
    users = (data.get("users") or {}) if isinstance(data, dict) else {}
    since = date.today() - timedelta(days=max(1, int(days or 30)) - 1)
    out: list[str] = []
    for user in users.values():
        by_chat = (user or {}).get("messages_by_chat") or {}
        for raw_chat_id, rows in by_chat.items():
            if chat_id is not None and str(raw_chat_id) != str(int(chat_id)):
                continue
            for row in rows or []:
                when = _parse_day((row or {}).get("date"))
                if when is not None and when < since:
                    continue
                text = str((row or {}).get("text", "") or "").strip()
                if not text:
                    continue
                out.append(text)
                if len(out) >= max(100, int(limit)):
                    return out
    return out


def _extract_topics(texts: list[str], *, top_n: int = 10) -> list[dict]:
    if not texts:
        return []
    token_counts: Counter[str] = Counter()
    for text in texts:
        for token in _WORD_RE.findall(str(text or "").lower()):
            t = token.strip("-")
            if len(t) < 4 or t in _STOP_WORDS:
                continue
            token_counts[t] += 1
    if not token_counts:
        return []
    total = sum(token_counts.values())
    out = []
    for topic, count in token_counts.most_common(max(1, int(top_n))):
        out.append(
            {
                "topic": topic,
                "count": int(count),
                "share": round(float(count) / max(1, total), 4),
            }
        )
    return out


def _aggregate_health(chat_ids: list[int], *, days: int) -> dict:
    # Ограничиваем числом чатов, чтобы дашборд «все чаты» не грузился минутами
    rows = [marketing_metrics.get_chat_health(cid, days=days) for cid in chat_ids[:12]]
    if not rows:
        return {
            "participants": 0,
            "avg_engagement": 0.0,
            "avg_retention": 0.0,
            "avg_toxicity": 0.0,
            "health_score": 0.0,
            "messages": 0,
        }
    return {
        "participants": int(sum(_safe_int(x.get("participants", 0), 0) for x in rows)),
        "avg_engagement": float(sum(_safe_float(x.get("avg_engagement", 0.0), 0.0) for x in rows) / len(rows)),
        "avg_retention": float(sum(_safe_float(x.get("avg_retention", 0.0), 0.0) for x in rows) / len(rows)),
        "avg_toxicity": float(sum(_safe_float(x.get("avg_toxicity", 0.0), 0.0) for x in rows) / len(rows)),
        "health_score": float(sum(_safe_float(x.get("health_score", 0.0), 0.0) for x in rows) / len(rows)),
        "messages": int(sum(_safe_int(x.get("messages", 0), 0) for x in rows)),
    }


def build_chat_health_dashboard(chat_id: int | None, *, days: int = 30) -> dict:
    safe_days = max(1, min(180, int(days or 30)))
    if chat_id is None:
        known = _chat_ids_known()
        health = _aggregate_health(known, days=safe_days)
        community = build_community_health(None)
        risk = build_moderation_risk(None)
        predictive = predict_overview(None, horizon_days=7, lookback_days=max(14, safe_days))
    else:
        health = marketing_metrics.get_chat_health(int(chat_id), days=safe_days)
        community = build_community_health(int(chat_id))
        risk = build_moderation_risk(int(chat_id))
        predictive = predict_overview(int(chat_id), horizon_days=7, lookback_days=max(14, safe_days))

    daily_counts = community.get("daily_counts") or []
    by_day = [float((x or {}).get("count", 0.0) or 0.0) for x in daily_counts]
    engagement_trend = _trend_ratio(by_day, window=7)

    churn_sig = ((predictive.get("signals") or {}).get("churn_risk") or {})
    tox_sig = ((predictive.get("signals") or {}).get("toxicity") or {})
    vir_sig = ((predictive.get("signals") or {}).get("virality") or {})
    retention_trend = -_safe_float(churn_sig.get("delta", 0.0), 0.0)
    toxicity_trend = -_safe_float(tox_sig.get("delta", 0.0), 0.0)
    health_trend = (engagement_trend * 0.4) + (retention_trend * 0.35) + (toxicity_trend * 0.25)
    virality_trend = _safe_float(vir_sig.get("delta", 0.0), 0.0)

    texts = _collect_messages(chat_id, days=safe_days, limit=2500)
    topics = _extract_topics(texts, top_n=5)
    if not topics:
        top_flags = (risk.get("top_red_flags") or [])[:5]
        topics = [{"topic": str(x.get("word") or "topic"), "count": int(x.get("count", 0) or 0), "share": 0.0} for x in top_flags]

    tone = (risk.get("tone_context") or {})
    bands = tone.get("bands") or {}
    pos = _safe_int(bands.get("positive", 0), 0)
    neu = _safe_int(bands.get("neutral", 0), 0)
    neg = _safe_int(bands.get("negative", 0), 0)
    total = max(1, pos + neu + neg)
    targets = {
        "health_score": 0.4,
        "engagement": 0.45,
        "retention": 0.5,
    }

    return {
        "chat_id": "all" if chat_id is None else int(chat_id),
        "days": int(safe_days),
        "health_score": round(_safe_float(health.get("health_score", 0.0), 0.0), 4),
        "health_trend": round(float(health_trend), 4),
        "engagement": round(_safe_float(health.get("avg_engagement", 0.0), 0.0), 4),
        "engagement_trend": round(float(engagement_trend), 4),
        "retention": round(_safe_float(health.get("avg_retention", 0.0), 0.0), 4),
        "retention_trend": round(float(retention_trend), 4),
        "toxicity": round(_safe_float(health.get("avg_toxicity", 0.0), 0.0), 4),
        "toxicity_trend": round(float(toxicity_trend), 4),
        "virality_trend": round(float(virality_trend), 4),
        "active_users_24h": int(community.get("dau", 0) or 0),
        "messages_today": int(by_day[-1] if by_day else 0),
        "participants": int(health.get("participants", 0) or 0),
        "health_status": str(health.get("health_status") or "unknown"),
        "dominant_topics": topics,
        "targets": targets,
        "target_semantics": "target_value",
        "sentiment_distribution": {
            "positive": round(pos / total, 4),
            "neutral": round(neu / total, 4),
            "negative": round(neg / total, 4),
        },
    }


def build_community_structure_dashboard(chat_id: int | None, *, period: str = "30d", limit: int = 1200) -> dict:
    from services.graph_api import build_graph_payload

    graph = build_graph_payload(chat_id, period=period, limit=max(200, min(int(limit or 1200), 5000)))
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    meta = graph.get("meta") or {}
    n = len(nodes)
    m = len(edges)
    density = (2.0 * m) / float(n * (n - 1)) if n > 1 else 0.0
    avg_degree = (2.0 * m) / float(max(1, n))

    comm_counter: Counter[int] = Counter(int((x or {}).get("community_id", 0) or 0) for x in nodes)
    largest_size = int(max(comm_counter.values()) if comm_counter else 0)
    largest_pct = float(largest_size / max(1, n))

    bridge_score: defaultdict[int, float] = defaultdict(float)
    for edge in edges:
        src = _safe_int(edge.get("source", 0), 0)
        dst = _safe_int(edge.get("target", 0), 0)
        if not src or not dst:
            continue
        b = _safe_float(edge.get("bridge_score", 0.0), 0.0)
        if b <= 0.0 and _safe_int(edge.get("community_id", 0), 0) == -1:
            b = 0.5
        bridge_score[src] += b
        bridge_score[dst] += b

    bridge_rows = []
    for node in nodes:
        uid = _safe_int(node.get("id", 0), 0)
        if not uid:
            continue
        centrality = _safe_float(node.get("centrality", 0.0), 0.0)
        score = _safe_float(bridge_score.get(uid, 0.0), 0.0) + centrality * 0.4
        bridge_rows.append(
            {
                "user_id": uid,
                "user_hash": user_hash(uid, chat_id),
                "username": str(node.get("label") or uid),
                "betweenness": round(float(score), 4),
            }
        )
    bridge_rows.sort(key=lambda x: float(x.get("betweenness", 0.0) or 0.0), reverse=True)

    return {
        "chat_id": "all" if chat_id is None else int(chat_id),
        "period": str(period or "30d"),
        "graph": graph,
        "density": round(float(density), 6),
        "avg_degree": round(float(avg_degree), 4),
        "clustering_coefficient": round(_safe_float(meta.get("content_diversity", 0.0), 0.0), 4),
        "modularity": round(_safe_float(meta.get("communities_modularity", 0.0), 0.0), 4),
        "number_of_communities": int(meta.get("communities_count", len(comm_counter)) or len(comm_counter)),
        "largest_community_size": int(largest_size),
        "largest_community_percentage": round(float(largest_pct), 4),
        "bridge_users": bridge_rows[:10],
    }


def build_users_list(
    chat_id: int | None,
    *,
    limit: int = 300,
) -> list[dict]:
    """Список пользователей чата: [{id, name}, ...] для выпадающих списков."""
    safe_limit = max(1, min(int(limit or 300), 500))
    display_names = user_stats.get_user_display_names()
    if chat_id is None:
        user_ids = list(display_names.keys())
    else:
        user_ids = user_stats.get_users_in_chat(int(chat_id))
    result = []
    for uid in user_ids:
        try:
            uid_int = int(uid)
        except (ValueError, TypeError):
            continue
        name = str(display_names.get(uid, uid))
        result.append({"id": uid_int, "name": name})
    result.sort(key=lambda x: (x["name"].lower(), str(x["id"])))
    return result[:safe_limit]


def build_user_leaderboard_dashboard(
    chat_id: int | None,
    *,
    metric: str = "engagement",
    limit: int = 10,
    days: int = 30,
) -> dict:
    metric_key = str(metric or "engagement")
    safe_days = max(1, min(int(days or 30), 180))
    safe_limit = max(1, min(int(limit or 10), 100))
    rows = marketing_metrics.get_leaderboard(
        metric=metric_key,
        chat_id=chat_id,
        days=safe_days,
        limit=safe_limit,
    )
    score_key_map = {
        "engagement": "engagement_score",
        "influence": "influence_score",
        "retention": "retention_score",
        "viral": "viral_coefficient",
        "churn": "churn_risk",
    }
    score_key = str(score_key_map.get(metric_key, "engagement_score"))
    users = []
    for row in rows:
        uid = _safe_int(row.get("user_id", 0), 0)
        current_score = round(_safe_float(row.get("score", 0.0), 0.0), 4)
        # Сравниваем два одинаковых по длине интервала: последние N дней vs предыдущие N дней (без смешения 30d с 60d).
        prev_window = marketing_metrics.get_user_metrics(
            uid, chat_id=chat_id, days=safe_days, window_end_offset_days=safe_days
        )
        previous_score = round(_safe_float(prev_window.get(score_key, 0.0), 0.0), 4)
        trend_delta = round(current_score - previous_score, 4)
        if abs(trend_delta) < 0.005:
            trend = "flat"
        else:
            trend = "up" if trend_delta > 0 else "down"
        activity_24h = marketing_metrics.get_user_metrics(uid, chat_id=chat_id, days=1)
        activity_24h_msgs = int((activity_24h.get("totals") or {}).get("messages", 0) or 0)
        users.append(
            {
                "rank": 0,
                "user_id": uid,
                "user_hash": user_hash(uid, chat_id),
                "username": str(row.get("display_name") or row.get("user_id") or ""),
                "score": current_score,
                "score_previous": previous_score,
                "trend": trend,
                "trend_delta": trend_delta,
                "activity_24h_messages": activity_24h_msgs,
                "details": {
                    "engagement": round(_safe_float(row.get("engagement_score", 0.0), 0.0), 4),
                    "influence": round(_safe_float(row.get("influence_score", 0.0), 0.0), 4),
                    "retention": round(_safe_float(row.get("retention_score", 0.0), 0.0), 4),
                    "viral_coefficient": round(_safe_float(row.get("viral_coefficient", 0.0), 0.0), 4),
                    "churn_risk": round(_safe_float(row.get("churn_risk", 0.0), 0.0), 4),
                },
            }
        )
    users.sort(
        key=lambda x: (
            -_safe_float(x.get("score", 0.0), 0.0),
            -_safe_int(x.get("activity_24h_messages", 0), 0),
            str(x.get("username") or ""),
        )
    )
    for idx, row in enumerate(users):
        row["rank"] = idx + 1
    return {
        "chat_id": "all" if chat_id is None else int(chat_id),
        "metric": metric_key,
        "limit": int(safe_limit),
        "users": users,
    }


def _retention_action(row: dict, *, days: int) -> dict:
    churn = _safe_float(row.get("churn_risk", 0.0), 0.0)
    influence = _safe_float(row.get("influence_score", 0.0), 0.0)
    active_days = _safe_int(row.get("active_days", 0), 0)
    streak = _safe_int(row.get("activity_streak", 0), 0)
    negative_share = _safe_float(row.get("negative_share", 0.0), 0.0)
    reach_factor = _safe_float(row.get("reach_factor", 0.0), 0.0)
    discussion_depth = _safe_float(row.get("discussion_depth", 0.0), 0.0)
    days_inactive = max(0, int(days) - active_days)
    if days_inactive >= max(3, int(days * 0.35)):
        action = "Мягкий ре-онбординг: личное сообщение + вопрос для возврата в диалог."
        reason_code = "activity_drop"
        reason = f"Резкое снижение активности: {days_inactive} дн. без устойчивого участия"
        trend = "dropping"
    elif negative_share >= 0.32:
        action = "Снизить конфликтную нагрузку: предложить нейтральную тему и ручную модерацию треда."
        reason_code = "conflict_rise"
        reason = f"Рост конфликтных сигналов: доля негатива {negative_share:.0%}"
        trend = "declining"
    elif reach_factor < 0.18 and discussion_depth < 0.22:
        action = "Интеграция в сообщество: упоминание в активной ветке + социальный мост."
        reason_code = "isolation"
        reason = f"Изоляция в графе: reach={reach_factor:.2f}, depth={discussion_depth:.2f}"
        trend = "isolated"
    elif streak >= 3:
        action = "Поддержать возвращение: закрепить участие в текущем обсуждении."
        reason_code = "retention_risk"
        reason = "Активность возвращается, но риск оттока сохраняется"
        trend = "returning"
    else:
        action = "Точечный пинг: дать вход в активный тред и попросить короткий комментарий."
        reason_code = "retention_risk"
        reason = f"Риск оттока: churn={churn:.2f}, influence={influence:.2f}"
        trend = "stable"
    dm = (
        f"Привет! Не хватает твоего голоса в обсуждениях. "
        f"Если хочешь, подключайся к ближайшей теме — будем рады твоему мнению."
    )
    return {
        "days_inactive": int(days_inactive),
        "activity_trend": trend,
        "recommended_action": action,
        "reason_code": reason_code,
        "reason": reason,
        "suggested_message": dm,
    }


def build_at_risk_users_dashboard(
    chat_id: int | None,
    *,
    threshold: float = 0.6,
    days: int = 30,
    limit: int = 30,
) -> dict:
    safe_days = max(1, min(int(days or 30), 180))
    safe_threshold = max(0.0, min(float(threshold or 0.6), 1.0))
    dashboard = build_retention_dashboard(chat_id, days=safe_days, limit=max(100, int(limit or 30)))
    rows = [
        x
        for x in (dashboard.get("at_risk") or [])
        if _safe_float((x or {}).get("churn_risk", 0.0), 0.0) >= safe_threshold
    ]
    rows.sort(key=lambda x: _safe_float((x or {}).get("churn_risk", 0.0), 0.0), reverse=True)

    users = []
    for row in rows[: max(1, min(int(limit or 30), 200))]:
        uid = _safe_int(row.get("user_id", 0), 0)
        action = _retention_action(row, days=safe_days)
        users.append(
            {
                "user_id": uid,
                "user_hash": user_hash(uid, chat_id),
                "username": str(row.get("display_name") or row.get("user_id") or ""),
                "churn_risk": round(_safe_float(row.get("churn_risk", 0.0), 0.0), 4),
                "retention_score": round(_safe_float(row.get("retention_score", 0.0), 0.0), 4),
                "engagement_score": round(_safe_float(row.get("engagement_score", 0.0), 0.0), 4),
                "influence_score": round(_safe_float(row.get("influence_score", 0.0), 0.0), 4),
                **action,
            }
        )
    return {
        "chat_id": "all" if chat_id is None else int(chat_id),
        "days": int(safe_days),
        "threshold": round(float(safe_threshold), 4),
        "count": len(users),
        "users": users,
    }


def _decision_confidence(row: dict) -> float:
    metrics = (row.get("metrics") or {}) if isinstance(row, dict) else {}
    scores = metrics.get("strategy_scores") or {}
    if not isinstance(scores, dict) or not scores:
        return 0.0
    vals = sorted([abs(_safe_float(v, 0.0)) for v in scores.values()], reverse=True)
    top = vals[0] if vals else 0.0
    second = vals[1] if len(vals) > 1 else 0.0
    if top <= 0.0:
        return 0.0
    return _clamp01((top - second) / top)


def build_decision_quality_dashboard(chat_id: int | None, *, period_days: int = 7) -> dict:
    safe_days = max(1, min(int(period_days or 7), 180))
    quality = get_decision_quality(chat_id=chat_id, days=safe_days)
    rows = get_recent_decisions(limit=1500, chat_id=chat_id, user_id=None)
    since = _now_ts() - (safe_days * 24 * 3600)
    selected = [x for x in rows if _safe_float((x or {}).get("ts", 0.0), 0.0) >= since]

    strategy_counter: Counter[str] = Counter(str((x or {}).get("strategy") or "unknown") for x in selected)
    strategy_quality = {str(x.get("strategy") or "unknown"): x for x in (quality.get("by_strategy") or [])}
    by_strategy = []
    for strategy, total in strategy_counter.most_common():
        sq = strategy_quality.get(strategy) or {}
        by_strategy.append(
            {
                "strategy": strategy,
                "total": int(total),
                "approved": int(round(_safe_float(sq.get("avg_score", 0.0), 0.0) * total)) if sq else 0,
                "approval_rate": round(_safe_float(sq.get("avg_score", 0.0), 0.0) * 0.5 + 0.5, 4)
                if sq
                else 0.0,
            }
        )

    conf_rows = [_decision_confidence(x) for x in selected]
    avg_conf = sum(conf_rows) / max(1, len(conf_rows))
    learning = feedback_summary(chat_id=chat_id, days=safe_days)
    variant_rows = list(learning.get("variant") or [])
    winner = "control"
    if variant_rows:
        winner = max(variant_rows, key=lambda x: (_safe_float(x.get("approval_rate", 0.0), 0.0), _safe_int(x.get("events", 0), 0))).get("name", "control")

    with_feedback = [x for x in selected if "feedback_score" in (x or {})]
    with_feedback.sort(key=lambda x: _safe_float((x or {}).get("ts", 0.0), 0.0))
    improvement_rate = 0.0
    if len(with_feedback) >= 8:
        mid = len(with_feedback) // 2
        old = with_feedback[:mid]
        new = with_feedback[mid:]
        old_avg = sum(_safe_float(x.get("feedback_score", 0.0), 0.0) for x in old) / max(1, len(old))
        new_avg = sum(_safe_float(x.get("feedback_score", 0.0), 0.0) for x in new) / max(1, len(new))
        improvement_rate = new_avg - old_avg

    approved_feedback = [x for x in selected if _safe_float((x or {}).get("feedback_score", 0.0), 0.0) > 0.0]
    users_reengaged = len({int(x.get("user_id")) for x in approved_feedback if x.get("user_id") is not None})
    churn_prevented = len([x for x in approved_feedback if str((x or {}).get("strategy") or "") in {"motivating", "gentle", "careful"}])
    viral_detected = len([x for x in selected if "encouragement" in str((x or {}).get("outcome") or "")])

    by_variant = []
    for row in variant_rows:
        by_variant.append(
            {
                "name": str(row.get("name") or "control"),
                "events": int(row.get("events", 0) or 0),
                "approval_rate": round(_safe_float(row.get("approval_rate", 0.0), 0.0), 4),
            }
        )

    return {
        "chat_id": "all" if chat_id is None else int(chat_id),
        "period_days": int(safe_days),
        "total_decisions": int(quality.get("total_decisions", len(selected)) or len(selected)),
        "approved_count": int(quality.get("approved", 0) or 0),
        "rejected_count": int(quality.get("rejected", 0) or 0),
        "approval_rate": round(_safe_float(quality.get("approval_rate", 0.0), 0.0), 4),
        "decision_quality": round((_safe_float(quality.get("avg_feedback_score", 0.0), 0.0) + 1.0) / 2.0, 4),
        "avg_confidence": round(float(avg_conf), 4),
        "by_strategy": by_strategy,
        "learning": {
            "feedback_collected": int(quality.get("feedback_count", len(with_feedback)) or len(with_feedback)),
            "model_retrains": 0,
            "improvement_rate": round(float(improvement_rate), 4),
        },
        "ab_test": {
            "active": bool(len(by_variant) > 1),
            "winner": str(winner),
            "variants": by_variant,
        },
        "results": {
            "churn_prevented": int(churn_prevented),
            "users_re_engaged": int(users_reengaged),
            "viral_moments_detected": int(viral_detected),
        },
    }


def build_content_analysis_dashboard(chat_id: int | None, *, period_days: int = 30) -> dict:
    safe_days = max(1, min(int(period_days or 30), 180))
    texts = _collect_messages(chat_id, days=safe_days, limit=3500)
    analyzer = ToneAnalyzer()
    scored = [analyzer.analyze_single(t) for t in texts]
    pos = len([x for x in scored if _safe_float(x.get("score", 0.0), 0.0) > 0.15])
    neg = len([x for x in scored if _safe_float(x.get("score", 0.0), 0.0) < -0.15])
    neu = len(scored) - pos - neg
    total = max(1, len(scored))

    topics = _extract_topics(texts, top_n=10)
    avg_len = sum(len(t) for t in texts) / max(1, len(texts))

    tokens = []
    for text in texts:
        tokens.extend([x.lower() for x in _WORD_RE.findall(text)])
    uniq = len(set(tokens))
    diversity = float(uniq / max(1, len(tokens))) if tokens else 0.0
    clarity = _clamp01((diversity * 1.8) + (min(1.0, avg_len / 240.0) * 0.2))
    informativeness = _clamp01((min(1.0, avg_len / 180.0) * 0.65) + (min(1.0, len(topics) / 10.0) * 0.35))

    topic_sentiment = []
    if topics:
        for row in topics[:10]:
            token = str(row.get("topic") or "")
            local_scores = [
                _safe_float(analyzer.analyze_single(t).get("score", 0.0), 0.0)
                for t in texts
                if token and token in t.lower()
            ]
            avg = sum(local_scores) / max(1, len(local_scores))
            topic_sentiment.append(
                {
                    "topic": token,
                    "messages": int(row.get("count", 0) or 0),
                    "sentiment": round(float(avg), 4),
                }
            )

    risk = build_moderation_risk(chat_id)
    top_flags = risk.get("top_red_flags") or []
    toxicity_hotspots = [
        {
            "topic": str(x.get("word") or ""),
            "toxicity": round(min(1.0, _safe_int(x.get("count", 0), 0) / max(1.0, len(texts) / 12.0)), 4),
            "count": int(x.get("count", 0) or 0),
        }
        for x in top_flags[:5]
    ]

    return {
        "chat_id": "all" if chat_id is None else int(chat_id),
        "period_days": int(safe_days),
        "top_topics": topic_sentiment,
        "sentiment": {
            "positive": round(pos / total, 4),
            "neutral": round(neu / total, 4),
            "negative": round(neg / total, 4),
        },
        "content_quality": {
            "avg_length": round(float(avg_len), 2),
            "avg_clarity": round(float(clarity), 4),
            "avg_informativeness": round(float(informativeness), 4),
        },
        "toxicity_hotspots": toxicity_hotspots,
    }


def build_moderation_activity_dashboard(chat_id: int | None, *, period_days: int = 7) -> dict:
    safe_days = max(1, min(int(period_days or 7), 90))
    community = build_community_health(chat_id)
    daily = community.get("daily_counts") or []
    total_messages = int(sum(_safe_int((x or {}).get("count", 0), 0) for x in daily[-safe_days:]))

    recent = get_recent_decisions(limit=1500, chat_id=chat_id, user_id=None)
    since = _now_ts() - (safe_days * 24 * 3600)
    selected = [x for x in recent if _safe_float((x or {}).get("ts", 0.0), 0.0) >= since]
    political = [x for x in selected if bool((x or {}).get("is_political"))]

    warnings = [
        x
        for x in selected
        if "warning" in str((x or {}).get("action_hint") or "").lower()
        or "warning" in str((x or {}).get("outcome") or "").lower()
    ]
    paused_users = {
        int(x.get("user_id"))
        for x in selected
        if x.get("user_id") is not None
        and (
            "pause" in str((x or {}).get("outcome") or "").lower()
            or "pause" in str((x or {}).get("detail") or "").lower()
        )
    }
    conf = [_decision_confidence(x) for x in selected]
    avg_conf = sum(conf) / max(1, len(conf))

    quality = get_decision_quality(chat_id=chat_id, days=safe_days)
    behavior_change = _safe_float(quality.get("approval_rate", 0.0), 0.0)
    return_rate = min(1.0, behavior_change + 0.15) if paused_users else 0.0
    prevented = int(round(len(warnings) * max(0.0, behavior_change)))

    timeline = []
    for row in selected[:12]:
        ts = _safe_float(row.get("ts", 0.0), 0.0)
        uid = _safe_int(row.get("user_id", 0), 0) if row.get("user_id") is not None else None
        timeline.append(
            {
                "ts": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts > 0 else "",
                "outcome": str(row.get("outcome") or ""),
                "strategy": str(row.get("strategy") or ""),
                "user_id": uid,
                "user_hash": user_hash(uid, chat_id) if uid is not None else None,
            }
        )

    risk = build_moderation_risk(chat_id)
    top_flags = risk.get("top_red_flags") or []

    return {
        "chat_id": "all" if chat_id is None else int(chat_id),
        "period_days": int(safe_days),
        "total_messages": int(total_messages),
        "political_detected": int(len(political)),
        "political_percentage": round(float(len(political) / max(1, len(selected))), 4) if selected else 0.0,
        "ai_decisions": int(len(selected)),
        "avg_confidence": round(float(avg_conf), 4),
        "effectiveness": {
            "warnings_issued": int(len(warnings)),
            "users_paused": int(len(paused_users)),
            "behavior_change_rate": round(float(behavior_change), 4),
            "return_rate": round(float(return_rate), 4),
            "conflicts_prevented": int(prevented),
        },
        "timeline": timeline,
        "top_political_topics": [{"topic": str(x.get("word") or ""), "count": int(x.get("count", 0) or 0)} for x in top_flags[:5]],
    }


def build_growth_trends_dashboard(chat_id: int | None, *, lookback_days: int = 30, horizon_days: int = 7) -> dict:
    safe_lookback = max(7, min(int(lookback_days or 30), 180))
    safe_horizon = max(1, min(int(horizon_days or 7), 30))
    snapshots = get_recent_churn_snapshots(limit=safe_lookback, chat_id=chat_id)
    rows = list(reversed(snapshots))

    users_series = []
    at_risk_series = []
    for row in rows:
        summary = row.get("summary") or {}
        users = _safe_int(summary.get("users_considered", summary.get("users_total", 0)), 0)
        at_risk = _safe_int(summary.get("at_risk_count", 0), 0)
        users_series.append(users)
        at_risk_series.append(at_risk)

    if not users_series:
        dashboard = build_retention_dashboard(chat_id, days=30, limit=200)
        summary = dashboard.get("summary") or {}
        users_series = [_safe_int(summary.get("users_considered", summary.get("users_total", 0)), 0)]
        at_risk_series = [_safe_int(summary.get("at_risk_count", 0), 0)]

    diffs = [users_series[i] - users_series[i - 1] for i in range(1, len(users_series))]
    new_users = int(sum(x for x in diffs if x > 0))
    churned = int(abs(sum(x for x in diffs if x < 0)))
    net_growth = int(users_series[-1] - users_series[0]) if users_series else 0
    growth_trend = "up" if net_growth > 0 else ("down" if net_growth < 0 else "flat")

    community = build_community_health(chat_id)
    daily = [float((x or {}).get("count", 0.0) or 0.0) for x in (community.get("daily_counts") or [])]
    engagement_trend = _trend_ratio(daily, window=7)

    risk_ratio = [float(at_risk_series[i] / max(1, users_series[i])) for i in range(min(len(users_series), len(at_risk_series)))]
    retention_trend = -_trend_ratio(risk_ratio, window=4) if len(risk_ratio) >= 8 else 0.0

    retention = build_retention_dashboard(chat_id, days=min(90, safe_lookback), limit=200)
    viral_moments = _safe_int(((retention.get("summary") or {}).get("viral_contributors_count", 0)), 0)
    forecast = predict_overview(chat_id, horizon_days=safe_horizon, lookback_days=max(14, safe_lookback))
    churn_pred = _safe_float(((forecast.get("signals") or {}).get("churn_risk") or {}).get("predicted", 0.0), 0.0)
    vir_pred = _safe_float(((forecast.get("signals") or {}).get("virality") or {}).get("predicted", 0.0), 0.0)

    health_now = _safe_float(marketing_metrics.get_chat_health(chat_id, days=30).get("health_score", 0.0), 0.0) if chat_id is not None else 0.0
    expected_health = _clamp01(health_now + ((engagement_trend + retention_trend) * 0.2) + ((vir_pred - 0.5) * 0.08))
    expected_churn_users = int(round(churn_pred * max(1, users_series[-1])))
    growth_per_day = float(net_growth / max(1, len(users_series) - 1)) if len(users_series) > 1 else 0.0
    expected_new_users = max(0, int(round((growth_per_day * safe_horizon) + max(0.0, vir_pred - 0.45) * 2.0)))

    return {
        "chat_id": "all" if chat_id is None else int(chat_id),
        "lookback_days": int(safe_lookback),
        "horizon_days": int(safe_horizon),
        "user_growth": {
            "new_users": int(new_users),
            "churned_users": int(churned),
            "net_growth": int(net_growth),
            "trend": growth_trend,
        },
        "engagement_trend": round(float(engagement_trend), 4),
        "retention_trend": round(float(retention_trend), 4),
        "viral_moments": int(viral_moments),
        "forecast": {
            "expected_health": round(float(expected_health), 4),
            "expected_churn_users": int(expected_churn_users),
            "expected_new_users": int(expected_new_users),
            "signals": forecast.get("signals") or {},
        },
    }
