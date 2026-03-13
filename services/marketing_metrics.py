from __future__ import annotations

import json
import logging
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import user_stats
from services.graph_api import build_graph_payload

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "marketing_metrics.json"
_DATA_LOCK = Lock()
_DEFAULT_WINDOW_DAYS = 30


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _day_key(ts: datetime | None = None) -> str:
    return (ts or _utc_now()).date().isoformat()


def _new_data() -> dict:
    return {"version": 1, "updated_at": _utc_now().isoformat(), "chats": {}}


def _load_data() -> dict:
    if not _DATA_PATH.exists():
        return _new_data()
    try:
        data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _new_data()
        data.setdefault("version", 1)
        data.setdefault("updated_at", _utc_now().isoformat())
        data.setdefault("chats", {})
        return data
    except Exception as exc:
        logger.warning("marketing_metrics load failed: %s", exc)
        return _new_data()


def _save_data(data: dict) -> None:
    _DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=_DATA_PATH.parent) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    tmp_path.replace(_DATA_PATH)


def _ensure_chat(data: dict, chat_id: int) -> dict:
    chats = data.setdefault("chats", {})
    key = str(int(chat_id))
    chat = chats.setdefault(
        key,
        {
            "user_daily": {},
            "user_meta": {},
            "chat_daily": {},
            "rollups": {},
        },
    )
    chat.setdefault("user_daily", {})
    chat.setdefault("user_meta", {})
    chat.setdefault("chat_daily", {})
    chat.setdefault("rollups", {})
    return chat


def _ensure_user_meta(chat: dict, user_id: int, display_name: str = "") -> dict:
    user_meta = chat["user_meta"].setdefault(
        str(int(user_id)),
        {
            "first_seen": "",
            "last_seen": "",
            "display_name": "",
            "last_message_ts": 0.0,
        },
    )
    if display_name:
        user_meta["display_name"] = display_name
    return user_meta


def _new_bucket() -> dict:
    return {
        "messages": 0,
        "replies_sent": 0,
        "replies_received": 0,
        "mentions_sent": 0,
        "mentions_received": 0,
        "response_count": 0,
        "response_total_sec": 0.0,
        "positive": 0,
        "negative": 0,
        "neutral": 0,
        "political": 0,
    }


def _ensure_user_day(chat: dict, user_id: int, day: str) -> dict:
    user_daily = chat["user_daily"].setdefault(str(int(user_id)), {})
    bucket = user_daily.setdefault(day, _new_bucket())
    for key, default in _new_bucket().items():
        bucket.setdefault(key, default)
    return bucket


def _ensure_chat_day(chat: dict, day: str) -> dict:
    day_bucket = chat["chat_daily"].setdefault(day, {"messages": 0, "toxic_messages": 0, "active_users": []})
    day_bucket.setdefault("messages", 0)
    day_bucket.setdefault("toxic_messages", 0)
    day_bucket.setdefault("active_users", [])
    return day_bucket


def _norm_sentiment(sentiment: str | None) -> str:
    raw = (sentiment or "").strip().lower()
    if raw in {"positive", "negative", "neutral"}:
        return raw
    return "neutral"


def _clamp01(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return float(value)


def _window_start_day(days: int) -> date:
    safe_days = max(1, int(days or _DEFAULT_WINDOW_DAYS))
    return _utc_now().date() - timedelta(days=safe_days - 1)


def _sum_bucket(dst: dict, src: dict) -> None:
    for key in _new_bucket():
        dst[key] = float(dst.get(key, 0.0) or 0.0) + float(src.get(key, 0.0) or 0.0)


def record_message_event(
    chat_id: int,
    user_id: int,
    *,
    display_name: str = "",
    reply_to_user_id: int | None = None,
    mentioned_user_ids: list[int] | None = None,
    sentiment: str | None = None,
    is_political: bool | None = None,
    timestamp: datetime | None = None,
) -> None:
    now = timestamp or _utc_now()
    now_ts = now.timestamp()
    day = now.date().isoformat()

    with _DATA_LOCK:
        data = _load_data()
        chat = _ensure_chat(data, chat_id)
        sender_bucket = _ensure_user_day(chat, user_id, day)
        sender_meta = _ensure_user_meta(chat, user_id, display_name=display_name)
        chat_day = _ensure_chat_day(chat, day)

        sender_bucket["messages"] += 1
        chat_day["messages"] += 1
        uid_key = str(int(user_id))
        if uid_key not in chat_day["active_users"]:
            chat_day["active_users"].append(uid_key)

        sender_meta["first_seen"] = sender_meta["first_seen"] or day
        sender_meta["last_seen"] = day
        sender_meta["last_message_ts"] = now_ts

        if reply_to_user_id and int(reply_to_user_id) != int(user_id):
            target_uid = int(reply_to_user_id)
            sender_bucket["replies_sent"] += 1
            target_bucket = _ensure_user_day(chat, target_uid, day)
            target_bucket["replies_received"] += 1
            target_meta = _ensure_user_meta(chat, target_uid)
            target_last = float(target_meta.get("last_message_ts", 0.0) or 0.0)
            if target_last > 0.0 and now_ts >= target_last:
                # Cap at 6h so long pauses do not destroy the score.
                delta = min(21600.0, now_ts - target_last)
                sender_bucket["response_count"] += 1
                sender_bucket["response_total_sec"] += delta

        mention_ids = sorted({int(x) for x in (mentioned_user_ids or []) if str(x).lstrip("-").isdigit()})
        for mentioned_uid in mention_ids:
            if mentioned_uid == int(user_id):
                continue
            sender_bucket["mentions_sent"] += 1
            mentioned_bucket = _ensure_user_day(chat, mentioned_uid, day)
            mentioned_bucket["mentions_received"] += 1
            _ensure_user_meta(chat, mentioned_uid)

        if sentiment is not None:
            sentiment_norm = _norm_sentiment(sentiment)
            sender_bucket[sentiment_norm] += 1
            if sentiment_norm == "negative":
                chat_day["toxic_messages"] += 1
        if is_political:
            sender_bucket["political"] += 1

        data["updated_at"] = _utc_now().isoformat()
        _save_data(data)


def record_signal_event(
    chat_id: int,
    user_id: int,
    *,
    sentiment: str | None = None,
    is_political: bool | None = None,
    timestamp: datetime | None = None,
) -> None:
    now = timestamp or _utc_now()
    day = now.date().isoformat()
    with _DATA_LOCK:
        data = _load_data()
        chat = _ensure_chat(data, chat_id)
        bucket = _ensure_user_day(chat, user_id, day)
        chat_day = _ensure_chat_day(chat, day)
        if sentiment is not None:
            sentiment_norm = _norm_sentiment(sentiment)
            bucket[sentiment_norm] += 1
            if sentiment_norm == "negative":
                chat_day["toxic_messages"] += 1
        if is_political:
            bucket["political"] += 1
        data["updated_at"] = _utc_now().isoformat()
        _save_data(data)


def _chat_ids(data: dict, chat_id: int | None) -> list[str]:
    if chat_id is None:
        return sorted((data.get("chats") or {}).keys())
    key = str(int(chat_id))
    return [key] if key in (data.get("chats") or {}) else []


def _aggregate_user(chat: dict, user_id: int, days: int) -> dict:
    start_day = _window_start_day(days).isoformat()
    user_days = (chat.get("user_daily") or {}).get(str(int(user_id)), {})
    agg = _new_bucket()
    active_days: set[str] = set()
    last_active = ""
    for day, bucket in user_days.items():
        if day < start_day:
            continue
        _sum_bucket(agg, bucket or {})
        if int((bucket or {}).get("messages", 0) or 0) > 0:
            active_days.add(day)
            if not last_active or day > last_active:
                last_active = day
    return {"totals": agg, "active_days": active_days, "last_active": last_active}


def _active_streak(active_days: set[str], days: int) -> int:
    if not active_days:
        return 0
    today = _utc_now().date()
    start = _window_start_day(days)
    streak = 0
    cursor = today
    while cursor >= start and cursor.isoformat() in active_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _graph_lookup(chat_id: int) -> dict[int, dict]:
    try:
        payload = build_graph_payload(chat_id, period="30d", limit=1600)
        nodes = payload.get("nodes") or []
    except Exception:
        nodes = []
    lookup: dict[int, dict] = {}
    for node in nodes:
        uid = int(node.get("id", 0) or 0)
        if not uid:
            continue
        centrality = float(node.get("centrality", 0.0) or 0.0)
        influence_raw = float(node.get("influence_score", 0.0) or 0.0)
        lookup[uid] = {
            "pagerank": _clamp01(centrality if centrality > 0.0 else influence_raw / 10.0),
            "reach": _clamp01(influence_raw / 10.0),
        }
    return lookup


def _resolve_display_name(data: dict, user_id: int, preferred: str = "") -> str:
    if preferred:
        return preferred
    uid_key = str(int(user_id))
    for chat in (data.get("chats") or {}).values():
        name = ((chat.get("user_meta") or {}).get(uid_key) or {}).get("display_name")
        if name:
            return str(name)
    try:
        users = (user_stats._load() or {}).get("users", {})  # type: ignore[attr-defined]
    except Exception:
        users = {}
    return str((users.get(uid_key) or {}).get("display_name") or uid_key)


def get_user_metrics(user_id: int, *, chat_id: int | None = None, days: int = _DEFAULT_WINDOW_DAYS) -> dict:
    safe_days = max(1, int(days or _DEFAULT_WINDOW_DAYS))
    with _DATA_LOCK:
        data = _load_data()

    chat_keys = _chat_ids(data, chat_id)
    if not chat_keys:
        return {
            "user_id": int(user_id),
            "chat_id": "all" if chat_id is None else int(chat_id),
            "days": safe_days,
            "display_name": _resolve_display_name(data, user_id),
            "totals": _new_bucket(),
            "engagement_score": 0.0,
            "influence_score": 0.0,
            "retention_score": 0.0,
            "churn_risk": 1.0,
            "viral_coefficient": 0.0,
            "content_quality_score": 0.0,
            "active_days": 0,
            "activity_streak": 0,
        }

    combined = _new_bucket()
    active_days: set[str] = set()
    latest_active = ""
    weighted_pagerank = 0.0
    weighted_reach = 0.0
    weighted_base = 0.0
    display_name = ""

    for chat_key in chat_keys:
        chat = (data.get("chats") or {}).get(chat_key) or {}
        agg = _aggregate_user(chat, user_id, safe_days)
        totals = agg["totals"]
        _sum_bucket(combined, totals)
        active_days |= set(agg["active_days"])
        if agg["last_active"] and agg["last_active"] > latest_active:
            latest_active = agg["last_active"]
        meta = (chat.get("user_meta") or {}).get(str(int(user_id))) or {}
        if not display_name:
            display_name = str(meta.get("display_name") or "")
        graph = _graph_lookup(int(chat_key))
        g_user = graph.get(int(user_id), {})
        weight = max(1.0, float(totals.get("messages", 0.0) or 0.0))
        weighted_base += weight
        weighted_pagerank += weight * float(g_user.get("pagerank", 0.0) or 0.0)
        weighted_reach += weight * float(g_user.get("reach", 0.0) or 0.0)

    messages = float(combined.get("messages", 0.0) or 0.0)
    replies_sent = float(combined.get("replies_sent", 0.0) or 0.0)
    replies_received = float(combined.get("replies_received", 0.0) or 0.0)
    mentions_received = float(combined.get("mentions_received", 0.0) or 0.0)
    mentions_sent = float(combined.get("mentions_sent", 0.0) or 0.0)
    response_count = float(combined.get("response_count", 0.0) or 0.0)
    response_total = float(combined.get("response_total_sec", 0.0) or 0.0)
    positive = float(combined.get("positive", 0.0) or 0.0)
    negative = float(combined.get("negative", 0.0) or 0.0)
    neutral = float(combined.get("neutral", 0.0) or 0.0)
    sentiment_total = max(1.0, positive + negative + neutral)

    reply_rate = _clamp01(replies_sent / max(1.0, messages))
    mention_frequency = _clamp01((mentions_received + 0.5 * mentions_sent) / max(1.0, messages))
    avg_response_sec = response_total / response_count if response_count > 0 else 0.0
    response_time_factor = _clamp01(1.0 - (avg_response_sec / 3600.0)) if response_count > 0 else 0.5
    discussion_depth = _clamp01((replies_sent + replies_received) / max(1.0, messages))
    engagement = (
        reply_rate * 0.3
        + mention_frequency * 0.2
        + response_time_factor * 0.2
        + discussion_depth * 0.3
    )

    pagerank = weighted_pagerank / weighted_base if weighted_base > 0 else 0.0
    reach_factor = _clamp01((mentions_received + replies_received) / max(1.0, messages))
    sentiment_shift = _clamp01(0.5 + ((positive - negative) / sentiment_total) * 0.5)
    influence = (
        _clamp01(pagerank) * 0.4
        + reach_factor * 0.3
        + reply_rate * 0.2
        + sentiment_shift * 0.1
    )

    days_active = len(active_days)
    days_active_factor = _clamp01(days_active / max(1.0, float(safe_days)))
    streak = _active_streak(active_days, safe_days)
    streak_factor = _clamp01(streak / 7.0)
    recency_factor = 0.0
    if latest_active:
        recency_days = (_utc_now().date() - date.fromisoformat(latest_active)).days
        recency_factor = _clamp01(1.0 - (float(recency_days) / 14.0))
    neg_share = negative / sentiment_total
    content_quality = _clamp01(1.0 - (neg_share * 0.8))
    retention = (
        days_active_factor * 0.3
        + streak_factor * 0.2
        + recency_factor * 0.3
        + content_quality * 0.2
    )

    viral_coeff = float(mentions_received / max(1.0, messages))
    churn_risk = _clamp01(1.0 - retention)

    out_totals = {k: round(float(v), 4) for k, v in combined.items()}
    return {
        "user_id": int(user_id),
        "chat_id": "all" if chat_id is None else int(chat_id),
        "days": safe_days,
        "display_name": _resolve_display_name(data, user_id, preferred=display_name),
        "totals": out_totals,
        "components": {
            "reply_rate": round(reply_rate, 4),
            "mention_frequency": round(mention_frequency, 4),
            "response_time_factor": round(response_time_factor, 4),
            "discussion_depth": round(discussion_depth, 4),
            "pagerank": round(pagerank, 4),
            "reach_factor": round(reach_factor, 4),
            "sentiment_shift": round(sentiment_shift, 4),
            "days_active_factor": round(days_active_factor, 4),
            "activity_streak_factor": round(streak_factor, 4),
            "recency_factor": round(recency_factor, 4),
        },
        "engagement_score": round(_clamp01(engagement), 4),
        "influence_score": round(_clamp01(influence), 4),
        "retention_score": round(_clamp01(retention), 4),
        "churn_risk": round(churn_risk, 4),
        "viral_coefficient": round(viral_coeff, 4),
        "content_quality_score": round(content_quality, 4),
        "active_days": int(days_active),
        "activity_streak": int(streak),
        "avg_response_sec": round(avg_response_sec, 2),
    }


def get_chat_health(chat_id: int, *, days: int = _DEFAULT_WINDOW_DAYS) -> dict:
    safe_days = max(1, int(days or _DEFAULT_WINDOW_DAYS))
    with _DATA_LOCK:
        data = _load_data()
    chat = (data.get("chats") or {}).get(str(int(chat_id))) or {}
    user_ids = sorted((chat.get("user_meta") or {}).keys())

    if not user_ids:
        return {
            "chat_id": int(chat_id),
            "days": safe_days,
            "participants": 0,
            "avg_engagement": 0.0,
            "avg_retention": 0.0,
            "avg_toxicity": 0.0,
            "content_diversity": 0.0,
            "health_score": 0.0,
            "health_status": "critical",
            "viral_coefficient": 0.0,
        }

    per_user = [get_user_metrics(int(uid), chat_id=int(chat_id), days=safe_days) for uid in user_ids]
    active = [row for row in per_user if float((row.get("totals") or {}).get("messages", 0.0) or 0.0) > 0.0]
    base = active or per_user

    avg_engagement = sum(float(row.get("engagement_score", 0.0) or 0.0) for row in base) / max(1, len(base))
    avg_retention = sum(float(row.get("retention_score", 0.0) or 0.0) for row in base) / max(1, len(base))
    viral = sum(float(row.get("viral_coefficient", 0.0) or 0.0) for row in base) / max(1, len(base))

    start_day = _window_start_day(safe_days).isoformat()
    chat_days = chat.get("chat_daily") or {}
    total_messages = 0
    toxic_messages = 0
    active_users_union: set[str] = set()
    for day, row in chat_days.items():
        if day < start_day:
            continue
        total_messages += int((row or {}).get("messages", 0) or 0)
        toxic_messages += int((row or {}).get("toxic_messages", 0) or 0)
        active_users_union |= set((row or {}).get("active_users") or [])
    toxicity = float(toxic_messages) / max(1.0, float(total_messages))
    content_diversity = _clamp01(len(active_users_union) / max(1.0, float(len(user_ids))))

    health_score = (
        avg_engagement * 0.3
        + avg_retention * 0.3
        + (1.0 - _clamp01(toxicity)) * 0.2
        + content_diversity * 0.2
    )
    if health_score >= 0.8:
        status = "healthy"
    elif health_score >= 0.5:
        status = "needs_attention"
    else:
        status = "critical"

    return {
        "chat_id": int(chat_id),
        "days": safe_days,
        "participants": len(user_ids),
        "avg_engagement": round(_clamp01(avg_engagement), 4),
        "avg_retention": round(_clamp01(avg_retention), 4),
        "avg_toxicity": round(_clamp01(toxicity), 4),
        "content_diversity": round(content_diversity, 4),
        "viral_coefficient": round(viral, 4),
        "health_score": round(_clamp01(health_score), 4),
        "health_status": status,
        "messages": int(total_messages),
        "toxic_messages": int(toxic_messages),
    }


def get_leaderboard(
    *,
    metric: str = "engagement",
    chat_id: int | None = None,
    days: int = _DEFAULT_WINDOW_DAYS,
    limit: int = 10,
) -> list[dict]:
    metric_key = (metric or "engagement").strip().lower()
    score_by_metric = {
        "engagement": "engagement_score",
        "influence": "influence_score",
        "retention": "retention_score",
        "viral": "viral_coefficient",
        "churn": "churn_risk",
        "churn_risk": "churn_risk",
    }
    score_key = score_by_metric.get(metric_key, "engagement_score")

    with _DATA_LOCK:
        data = _load_data()
    chat_keys = _chat_ids(data, chat_id)
    user_ids: set[str] = set()
    for chat_key in chat_keys:
        chat = (data.get("chats") or {}).get(chat_key) or {}
        user_ids |= set((chat.get("user_meta") or {}).keys())

    rows = []
    for uid in user_ids:
        metrics = get_user_metrics(int(uid), chat_id=chat_id, days=days)
        score = float(metrics.get(score_key, 0.0) or 0.0)
        rows.append(
            {
                "user_id": int(uid),
                "display_name": metrics.get("display_name") or uid,
                "score": round(score, 4),
                "engagement_score": metrics.get("engagement_score", 0.0),
                "influence_score": metrics.get("influence_score", 0.0),
                "retention_score": metrics.get("retention_score", 0.0),
                "churn_risk": metrics.get("churn_risk", 1.0),
                "viral_coefficient": metrics.get("viral_coefficient", 0.0),
            }
        )

    reverse = True
    rows.sort(key=lambda row: float(row.get("score", 0.0) or 0.0), reverse=reverse)
    return rows[: max(1, min(int(limit or 10), 100))]


def run_daily_rollups(*, days: int = _DEFAULT_WINDOW_DAYS, chat_id: int | None = None) -> int:
    safe_days = max(1, int(days or _DEFAULT_WINDOW_DAYS))
    with _DATA_LOCK:
        data = _load_data()
        chat_keys = _chat_ids(data, chat_id)
        for chat_key in chat_keys:
            chat = (data.get("chats") or {}).get(chat_key) or {}
            user_ids = sorted((chat.get("user_meta") or {}).keys())
            user_rollups = {
                uid: get_user_metrics(int(uid), chat_id=int(chat_key), days=safe_days)
                for uid in user_ids
            }
            health = get_chat_health(int(chat_key), days=safe_days)
            leaderboard_eng = get_leaderboard(metric="engagement", chat_id=int(chat_key), days=safe_days, limit=10)
            leaderboard_inf = get_leaderboard(metric="influence", chat_id=int(chat_key), days=safe_days, limit=10)
            leaderboard_ret = get_leaderboard(metric="retention", chat_id=int(chat_key), days=safe_days, limit=10)
            chat["rollups"] = {
                "generated_at": _utc_now().isoformat(),
                "days": safe_days,
                "user": user_rollups,
                "chat": health,
                "leaderboards": {
                    "engagement": leaderboard_eng,
                    "influence": leaderboard_inf,
                    "retention": leaderboard_ret,
                },
            }
        data["updated_at"] = _utc_now().isoformat()
        _save_data(data)
    return len(chat_keys)
