from __future__ import annotations

import json
import logging
import tempfile
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from threading import Lock

import user_stats
from services.graph_api import build_graph_payload
from services.storage_cutover import (
    storage_db_reads_enabled,
    storage_db_writes_enabled,
    storage_json_fallback_enabled,
    storage_json_writes_enabled,
    storage_db_only_mode,
)
from services import marketing_metrics_db as mm_db
from db.sync_engine import sync_session_scope

logger = logging.getLogger(__name__)

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "marketing_metrics.json"
_DATA_LOCK = Lock()
_DEFAULT_WINDOW_DAYS = 30


def _reads_metrics_from_db() -> bool:
    """При dual/db_first/db_only метрики читаются из БД (messages + marketing_signal_events), не из JSON."""
    return storage_db_reads_enabled()


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
    from services.storage_cutover import storage_json_writes_enabled
    if not storage_json_writes_enabled():
        return
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


def invalidate_graph_rows_cache() -> None:
    """Сброс кэша pagerank/reach по чатам (например после refresh рейтинга в админке)."""
    _graph_rows_cached.cache_clear()


@lru_cache(maxsize=64)
def _graph_rows_cached(chat_id: int, period: str, limit: int) -> tuple[tuple[int, float, float], ...]:
    """Pagerank/reach по узлам графа для одного чата. Кэш: без этого get_user_metrics × N
    вызывал build_graph_payload N раз и мог вешать админку на минуты."""
    try:
        payload = build_graph_payload(chat_id, period=period, limit=limit)
        nodes = payload.get("nodes") or []
    except Exception:
        nodes = []
    rows: list[tuple[int, float, float]] = []
    for node in nodes:
        uid = int(node.get("id", 0) or 0)
        if not uid:
            continue
        centrality = float(node.get("centrality", 0.0) or 0.0)
        influence_raw = float(node.get("influence_score", 0.0) or 0.0)
        rows.append(
            (
                uid,
                _clamp01(centrality if centrality > 0.0 else influence_raw / 10.0),
                _clamp01(influence_raw / 10.0),
            )
        )
    return tuple(rows)


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
    if storage_db_only_mode() and storage_db_writes_enabled():
        # В db_only счётчики только из БД (ingest_message_event).
        return
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
    if storage_db_writes_enabled():
        try:
            mm_db.insert_marketing_signal(
                chat_id=int(chat_id),
                user_id=int(user_id),
                occurred_at=now,
                sentiment=sentiment,
                is_political=is_political,
            )
        except Exception as exc:
            logger.debug("marketing_signal db insert failed: %s", exc)
        if not storage_json_writes_enabled():
            return
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


def _aggregate_user(chat: dict, user_id: int, days: int, *, end_offset_days: int = 0) -> dict:
    """Агрегация user_daily за ``days`` дней, окно заканчивается (сегодня − end_offset_days)."""
    safe_days = max(1, int(days))
    off = max(0, int(end_offset_days))
    end_d = _utc_now().date() - timedelta(days=off)
    start_d = end_d - timedelta(days=safe_days - 1)
    start_day = start_d.isoformat()
    end_day = end_d.isoformat()
    user_days = (chat.get("user_daily") or {}).get(str(int(user_id)), {})
    agg = _new_bucket()
    active_days: set[str] = set()
    last_active = ""
    for day, bucket in user_days.items():
        if day < start_day or day > end_day:
            continue
        _sum_bucket(agg, bucket or {})
        if int((bucket or {}).get("messages", 0) or 0) > 0:
            active_days.add(day)
            if not last_active or day > last_active:
                last_active = day
    return {"totals": agg, "active_days": active_days, "last_active": last_active}


def _active_streak(active_days: set[str], days: int, *, end_anchor: date | None = None) -> int:
    if not active_days:
        return 0
    anchor = end_anchor or _utc_now().date()
    safe_days = max(1, int(days))
    start = anchor - timedelta(days=safe_days - 1)
    streak = 0
    cursor = anchor
    while cursor >= start and cursor.isoformat() in active_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _graph_lookup(chat_id: int) -> dict[int, dict]:
    lookup: dict[int, dict] = {}
    for uid, pagerank, reach in _graph_rows_cached(int(chat_id), "30d", 1600):
        lookup[uid] = {"pagerank": pagerank, "reach": reach}
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


def _empty_user_metrics(user_id: int, chat_id: int | None, safe_days: int) -> dict:
    with _DATA_LOCK:
        data = _load_data()
    z = _new_bucket()
    return {
        "user_id": int(user_id),
        "chat_id": "all" if chat_id is None else int(chat_id),
        "days": safe_days,
        "display_name": _resolve_display_name(data, user_id),
        "totals": z,
        "components": {
            "reply_rate": 0.0,
            "mention_frequency": 0.0,
            "response_time_factor": 0.5,
            "discussion_depth": 0.0,
            "pagerank": 0.0,
            "reach_factor": 0.0,
            "sentiment_shift": 0.5,
            "days_active_factor": 0.0,
            "activity_streak_factor": 0.0,
            "recency_factor": 0.0,
        },
        "engagement_score": 0.0,
        "influence_score": 0.0,
        "retention_score": 0.0,
        "churn_risk": 1.0,
        "viral_coefficient": 0.0,
        "content_quality_score": 0.0,
        "active_days": 0,
        "activity_streak": 0,
        "avg_response_sec": 0.0,
    }


def _finalize_user_metrics_from_buckets(
    *,
    user_id: int,
    chat_id_out: int | str,
    safe_days: int,
    display_name: str,
    combined: dict,
    active_days: set[str],
    latest_active: str,
    weighted_pagerank: float,
    weighted_reach: float,
    weighted_base: float,
    end_anchor: date,
) -> dict:
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
    streak = _active_streak(active_days, safe_days, end_anchor=end_anchor)
    streak_factor = _clamp01(streak / 7.0)
    recency_factor = 0.0
    if latest_active:
        recency_days = (end_anchor - date.fromisoformat(latest_active)).days
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
        "chat_id": chat_id_out,
        "days": safe_days,
        "display_name": display_name,
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


def get_user_metrics(
    user_id: int,
    *,
    chat_id: int | None = None,
    days: int = _DEFAULT_WINDOW_DAYS,
    window_end_offset_days: int = 0,
) -> dict:
    """Метрики за последние ``days`` дней. ``window_end_offset_days`` сдвигает окно назад
    (например offset=30 и days=30 = предыдущий месяц для сравнения трендов)."""
    safe_days = max(1, int(days or _DEFAULT_WINDOW_DAYS))
    win_off = max(0, int(window_end_offset_days))

    if _reads_metrics_from_db():
        raw = mm_db.get_user_metrics_from_db(
            user_id=user_id,
            chat_id=chat_id,
            days=safe_days,
            window_end_offset_days=win_off,
            graph_for_chat=_graph_lookup,
        )
        if raw is not None:
            return _finalize_user_metrics_from_buckets(
                user_id=raw["user_id"],
                chat_id_out=raw["chat_id"],
                safe_days=raw["days"],
                display_name=str(raw["display_name"] or ""),
                combined=raw["combined"],
                active_days=raw["active_days"],
                latest_active=str(raw["latest_active"] or ""),
                weighted_pagerank=float(raw["weighted_pagerank"] or 0.0),
                weighted_reach=float(raw["weighted_reach"] or 0.0),
                weighted_base=float(raw["weighted_base"] or 0.0),
                end_anchor=raw["end_anchor"],
            )
        if not storage_json_fallback_enabled():
            return _empty_user_metrics(user_id, chat_id, safe_days)

    with _DATA_LOCK:
        data = _load_data()

    chat_keys = _chat_ids(data, chat_id)
    if not chat_keys:
        return _empty_user_metrics(user_id, chat_id, safe_days)

    combined = _new_bucket()
    active_days: set[str] = set()
    latest_active = ""
    weighted_pagerank = 0.0
    weighted_reach = 0.0
    weighted_base = 0.0
    display_name = ""

    end_anchor = _utc_now().date() - timedelta(days=win_off)
    for chat_key in chat_keys:
        chat = (data.get("chats") or {}).get(chat_key) or {}
        agg = _aggregate_user(chat, user_id, safe_days, end_offset_days=win_off)
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

    resolved_name = _resolve_display_name(data, user_id, preferred=display_name)
    return _finalize_user_metrics_from_buckets(
        user_id=int(user_id),
        chat_id_out="all" if chat_id is None else int(chat_id),
        safe_days=safe_days,
        display_name=resolved_name,
        combined=combined,
        active_days=active_days,
        latest_active=latest_active,
        weighted_pagerank=weighted_pagerank,
        weighted_reach=weighted_reach,
        weighted_base=weighted_base,
        end_anchor=end_anchor,
    )


def get_chat_health(chat_id: int, *, days: int = _DEFAULT_WINDOW_DAYS) -> dict:
    safe_days = max(1, int(days or _DEFAULT_WINDOW_DAYS))

    def _empty_health(*, with_counts: bool = False, total_messages: int = 0, toxic_messages: int = 0) -> dict:
        out = {
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
        if with_counts:
            out["messages"] = int(total_messages)
            out["toxic_messages"] = int(toxic_messages)
        return out

    if _reads_metrics_from_db():
        ctx = mm_db.load_chat_health_context(chat_id=int(chat_id), days=safe_days)
        if ctx is not None:
            user_ids = [str(u) for u in ctx["user_ids"]]
            if not user_ids:
                return _empty_health(
                    with_counts=True,
                    total_messages=int(ctx["total_messages"]),
                    toxic_messages=int(ctx["toxic_messages"]),
                )
            per_user = [get_user_metrics(int(uid), chat_id=int(chat_id), days=safe_days) for uid in user_ids]
            active = [row for row in per_user if float((row.get("totals") or {}).get("messages", 0.0) or 0.0) > 0.0]
            base = active or per_user
            avg_engagement = sum(float(row.get("engagement_score", 0.0) or 0.0) for row in base) / max(1, len(base))
            avg_retention = sum(float(row.get("retention_score", 0.0) or 0.0) for row in base) / max(1, len(base))
            viral = sum(float(row.get("viral_coefficient", 0.0) or 0.0) for row in base) / max(1, len(base))
            total_messages = int(ctx["total_messages"])
            toxic_messages = int(ctx["toxic_messages"])
            active_users_union = set(ctx["active_union"])
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
                "messages": total_messages,
                "toxic_messages": toxic_messages,
            }
        if not storage_json_fallback_enabled():
            return _empty_health()

    with _DATA_LOCK:
        data = _load_data()
    chat = (data.get("chats") or {}).get(str(int(chat_id))) or {}
    user_ids = sorted((chat.get("user_meta") or {}).keys())

    if not user_ids:
        return _empty_health()

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

    user_ids: set[str] = set()
    if _reads_metrics_from_db():
        try:
            start_dt, end_dt = mm_db.metrics_window_boundaries(max(1, int(days or _DEFAULT_WINDOW_DAYS)), 0)
            with sync_session_scope() as s:
                if chat_id is None:
                    raw_u = mm_db.distinct_user_ids_all_chats(s, start_dt, end_dt)
                else:
                    raw_u = mm_db.distinct_user_ids_in_chat(s, int(chat_id), start_dt, end_dt)
            user_ids = {str(u) for u in raw_u}
        except Exception as exc:
            logger.warning("leaderboard: db user list failed: %s", exc)
            user_ids = set()
        if not user_ids and storage_json_fallback_enabled():
            with _DATA_LOCK:
                data = _load_data()
            chat_keys = _chat_ids(data, chat_id)
            for chat_key in chat_keys:
                chat = (data.get("chats") or {}).get(chat_key) or {}
                user_ids |= set((chat.get("user_meta") or {}).keys())
    else:
        with _DATA_LOCK:
            data = _load_data()
        chat_keys = _chat_ids(data, chat_id)
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
    """Считает rollup по чатам без удержания _DATA_LOCK на всём цикле.

    Раньше внешний ``with _DATA_LOCK`` оборачивал вызовы get_user_metrics/get_chat_health,
    каждый из которых снова берёт тот же Lock → в одном потоке взаимная блокировка (бот «замирал»).
    Дополнительно долгий захват блокировал record_message_event в основном asyncio-потоке.
    """
    safe_days = max(1, int(days or _DEFAULT_WINDOW_DAYS))
    chat_keys: list[str] = []
    data: dict = {}

    start_dt, end_dt = mm_db.metrics_window_boundaries(safe_days, 0)
    if _reads_metrics_from_db():
        try:
            with sync_session_scope() as s:
                keys_int = mm_db.all_distinct_chat_ids(s) if chat_id is None else [int(chat_id)]
            chat_keys = [str(k) for k in keys_int]
        except Exception as exc:
            logger.warning("rollups: db chat list failed: %s", exc)
            chat_keys = []
        if not chat_keys and storage_json_fallback_enabled():
            with _DATA_LOCK:
                data = _load_data()
                chat_keys = _chat_ids(data, chat_id)
    else:
        with _DATA_LOCK:
            data = _load_data()
            chat_keys = _chat_ids(data, chat_id)

    rollups_by_chat: dict[str, dict] = {}
    for chat_key in chat_keys:
        cid = int(chat_key)
        user_ids: list[str] = []
        if _reads_metrics_from_db():
            try:
                with sync_session_scope() as s:
                    user_ids = [str(u) for u in mm_db.distinct_user_ids_in_chat(s, cid, start_dt, end_dt)]
            except Exception:
                user_ids = []
            if not user_ids and storage_json_fallback_enabled():
                with _DATA_LOCK:
                    if not data:
                        data = _load_data()
                chat_snap = (data.get("chats") or {}).get(chat_key) or {}
                user_ids = sorted((chat_snap.get("user_meta") or {}).keys())
        else:
            with _DATA_LOCK:
                if not data:
                    data = _load_data()
            chat_snap = (data.get("chats") or {}).get(chat_key) or {}
            user_ids = sorted((chat_snap.get("user_meta") or {}).keys())
        user_rollups = {uid: get_user_metrics(int(uid), chat_id=cid, days=safe_days) for uid in user_ids}
        health = get_chat_health(cid, days=safe_days)
        leaderboard_eng = get_leaderboard(metric="engagement", chat_id=cid, days=safe_days, limit=10)
        leaderboard_inf = get_leaderboard(metric="influence", chat_id=cid, days=safe_days, limit=10)
        leaderboard_ret = get_leaderboard(metric="retention", chat_id=cid, days=safe_days, limit=10)
        rollups_by_chat[chat_key] = {
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

    if storage_json_writes_enabled():
        with _DATA_LOCK:
            data = _load_data()
            for chat_key, rollup_payload in rollups_by_chat.items():
                chat = _ensure_chat(data, int(chat_key))
                chat["rollups"] = rollup_payload
            data["updated_at"] = _utc_now().isoformat()
            _save_data(data)
    return len(chat_keys)
