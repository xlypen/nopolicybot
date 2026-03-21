"""Агрегация маркетинговых метрик из PostgreSQL/SQLite (таблицы messages, marketing_signal_events)."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from db.models import MarketingSignalEvent, Message, User
from db.sync_engine import sync_session_scope

logger = logging.getLogger(__name__)


def _utc_today() -> date:
    return datetime.now(tz=timezone.utc).date()


def _window_datetimes(*, days: int, end_offset_days: int) -> tuple[datetime, datetime]:
    """Начало (включительно) и конец (исключительно) окна в наивном UTC."""
    safe_days = max(1, int(days))
    off = max(0, int(end_offset_days))
    end_d = _utc_today() - timedelta(days=off)
    start_d = end_d - timedelta(days=safe_days - 1)
    start_dt = datetime(start_d.year, start_d.month, start_d.day)
    end_dt = datetime(end_d.year, end_d.month, end_d.day) + timedelta(days=1)
    return start_dt, end_dt


def _end_anchor_date(*, end_offset_days: int) -> date:
    off = max(0, int(end_offset_days))
    return _utc_today() - timedelta(days=off)


def new_bucket() -> dict[str, float]:
    return {
        "messages": 0.0,
        "replies_sent": 0.0,
        "replies_received": 0.0,
        "mentions_sent": 0.0,
        "mentions_received": 0.0,
        "response_count": 0.0,
        "response_total_sec": 0.0,
        "positive": 0.0,
        "negative": 0.0,
        "neutral": 0.0,
        "political": 0.0,
    }


def insert_marketing_signal(
    *,
    chat_id: int,
    user_id: int,
    occurred_at: datetime,
    sentiment: str | None,
    is_political: bool | None,
) -> None:
    raw = (sentiment or "").strip().lower()
    if raw not in {"positive", "negative", "neutral"}:
        raw = "neutral"
    ts = occurred_at
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    row = MarketingSignalEvent(
        chat_id=int(chat_id),
        user_id=int(user_id),
        occurred_at=ts,
        sentiment=raw,
        is_political=bool(is_political),
    )
    with sync_session_scope() as session:
        session.add(row)


def _display_name(session: Session, user_id: int) -> str:
    u = session.get(User, int(user_id))
    if not u:
        return str(int(user_id))
    parts = [str(u.first_name or "").strip(), str(u.last_name or "").strip()]
    name = " ".join(p for p in parts if p).strip()
    if name:
        return name
    if u.username:
        return str(u.username).strip()
    return str(int(user_id))


def _iter_mentions(raw: Any) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    if isinstance(raw, list):
        for x in raw:
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
    return out


def aggregate_user_for_chat(
    session: Session,
    *,
    user_id: int,
    chat_id: int,
    start_dt: datetime,
    end_dt: datetime,
) -> dict[str, Any]:
    """Одна агрегация пользователя в одном чате за окно [start_dt, end_dt)."""
    uid = int(user_id)
    cid = int(chat_id)
    q = (
        select(Message)
        .where(
            Message.chat_id == cid,
            Message.sent_at >= start_dt,
            Message.sent_at < end_dt,
        )
        .order_by(Message.sent_at.asc())
    )
    rows = list(session.scalars(q).all())

    bucket = new_bucket()
    active_days: set[str] = set()
    last_active = ""
    last_sent: dict[int, datetime] = {}

    for m in rows:
        m_uid = int(m.user_id or 0)
        s = m.sent_at
        if s.tzinfo is not None:
            s = s.replace(tzinfo=None)
        day = s.date().isoformat()

        if m_uid == uid:
            bucket["messages"] += 1.0
            active_days.add(day)
            if not last_active or day > last_active:
                last_active = day

            for mid in _iter_mentions(m.mention_user_ids):
                if mid != uid:
                    bucket["mentions_sent"] += 1.0

            rt = m.replied_to
            if rt is not None and int(rt) != uid:
                bucket["replies_sent"] += 1.0
                b = int(rt)
                prev = last_sent.get(b)
                if prev is not None:
                    delta = (s - prev).total_seconds()
                    if delta >= 0:
                        bucket["response_count"] += 1.0
                        bucket["response_total_sec"] += min(21600.0, float(delta))

            flags = m.risk_flags or []
            if isinstance(flags, list) and "politics" in flags:
                bucket["political"] += 1.0

        if m_uid and m_uid != uid:
            rt = m.replied_to
            if rt is not None and int(rt) == uid:
                bucket["replies_received"] += 1.0

        if m_uid and m_uid != uid:
            for mid in _iter_mentions(m.mention_user_ids):
                if mid == uid:
                    bucket["mentions_received"] += 1.0

        if m_uid:
            last_sent[m_uid] = s

    # Сигналы ИИ
    sq = select(MarketingSignalEvent).where(
        MarketingSignalEvent.chat_id == cid,
        MarketingSignalEvent.user_id == uid,
        MarketingSignalEvent.occurred_at >= start_dt,
        MarketingSignalEvent.occurred_at < end_dt,
    )
    sigs = list(session.scalars(sq).all())
    sig_pos = sig_neg = sig_neu = 0
    sig_pol = 0
    for ev in sigs:
        if ev.sentiment == "positive":
            sig_pos += 1
        elif ev.sentiment == "negative":
            sig_neg += 1
        else:
            sig_neu += 1
        if ev.is_political:
            sig_pol += 1
        ev_day = ev.occurred_at.date().isoformat() if ev.occurred_at else ""
        if ev_day:
            active_days.add(ev_day)
            if not last_active or ev_day > last_active:
                last_active = ev_day

    if sig_pos or sig_neg or sig_neu:
        bucket["positive"] += float(sig_pos)
        bucket["negative"] += float(sig_neg)
        bucket["neutral"] += float(sig_neu)
    else:
        # Fallback: tone_score сообщений пользователя
        for m in rows:
            if int(m.user_id or 0) != uid:
                continue
            ts = m.tone_score
            if ts is None:
                continue
            if ts > 0.25:
                bucket["positive"] += 1.0
            elif ts < -0.25:
                bucket["negative"] += 1.0
            else:
                bucket["neutral"] += 1.0

    bucket["political"] += float(sig_pol)

    return {"totals": bucket, "active_days": active_days, "last_active": last_active}


def distinct_chats_for_user(session: Session, user_id: int, start_dt: datetime, end_dt: datetime) -> list[int]:
    q = (
        select(Message.chat_id)
        .where(
            Message.user_id == int(user_id),
            Message.sent_at >= start_dt,
            Message.sent_at < end_dt,
        )
        .distinct()
    )
    return sorted({int(x) for x in session.scalars(q).all()})


def all_distinct_chat_ids(session: Session) -> list[int]:
    q = select(Message.chat_id).distinct()
    return sorted({int(x) for x in session.scalars(q).all()})


def distinct_user_ids_in_chat(session: Session, chat_id: int, start_dt: datetime, end_dt: datetime) -> list[int]:
    q_msg = (
        select(Message.user_id)
        .where(
            Message.chat_id == int(chat_id),
            Message.sent_at >= start_dt,
            Message.sent_at < end_dt,
            Message.user_id.isnot(None),
        )
        .distinct()
    )
    ids = {int(x) for x in session.scalars(q_msg).all() if x is not None}
    return sorted(ids)


def distinct_user_ids_all_chats(session: Session, start_dt: datetime, end_dt: datetime) -> list[int]:
    q = (
        select(Message.user_id)
        .where(
            Message.sent_at >= start_dt,
            Message.sent_at < end_dt,
            Message.user_id.isnot(None),
        )
        .distinct()
    )
    return sorted({int(x) for x in session.scalars(q).all() if x is not None})


def chat_health_counts(
    session: Session,
    *,
    chat_id: int,
    start_dt: datetime,
    end_dt: datetime,
) -> tuple[int, int, set[str]]:
    """total_messages, toxic_messages, active_users (union of days as uid strings for diversity)."""
    cid = int(chat_id)
    total = int(
        session.scalar(
            select(func.count(Message.id)).where(
                Message.chat_id == cid,
                Message.sent_at >= start_dt,
                Message.sent_at < end_dt,
            )
        )
        or 0
    )
    neg_sig = int(
        session.scalar(
            select(func.count(MarketingSignalEvent.id)).where(
                MarketingSignalEvent.chat_id == cid,
                MarketingSignalEvent.occurred_at >= start_dt,
                MarketingSignalEvent.occurred_at < end_dt,
                MarketingSignalEvent.sentiment == "negative",
            )
        )
        or 0
    )
    neg_tone = int(
        session.scalar(
            select(func.count(Message.id)).where(
                Message.chat_id == cid,
                Message.sent_at >= start_dt,
                Message.sent_at < end_dt,
                Message.tone_score.isnot(None),
                Message.tone_score < -0.25,
            )
        )
        or 0
    )
    toxic = neg_sig + neg_tone

    active_union: set[str] = set()
    qm = select(Message.user_id, Message.sent_at).where(
        Message.chat_id == cid,
        Message.sent_at >= start_dt,
        Message.sent_at < end_dt,
        Message.user_id.isnot(None),
    )
    for uid, _st in session.execute(qm).all():
        if uid is not None:
            active_union.add(str(int(uid)))

    return total, toxic, active_union


def metrics_window_boundaries(days: int, end_offset_days: int = 0) -> tuple[datetime, datetime]:
    return _window_datetimes(days=days, end_offset_days=end_offset_days)


def load_chat_health_context(*, chat_id: int, days: int) -> dict[str, Any] | None:
    """Участники и агрегаты чата за окно для get_chat_health."""
    safe_days = max(1, int(days))
    try:
        start_dt, end_dt = metrics_window_boundaries(safe_days, 0)
        with sync_session_scope() as session:
            uids = distinct_user_ids_in_chat(session, int(chat_id), start_dt, end_dt)
            total, toxic, active_union = chat_health_counts(
                session, chat_id=int(chat_id), start_dt=start_dt, end_dt=end_dt
            )
        return {
            "user_ids": uids,
            "total_messages": total,
            "toxic_messages": toxic,
            "active_union": active_union,
        }
    except Exception as e:
        logger.warning("load_chat_health_context failed chat=%s: %s", chat_id, e)
        return None


def chat_daily_series(
    *,
    chat_id: int,
    lookback_days: int,
) -> list[tuple[str, dict[str, Any]]]:
    """Ряды по дням для predict_toxicity / predict_virality (совместимо с JSON chat_daily)."""
    lb = max(1, int(lookback_days))
    end_d = _utc_today()
    start_d = end_d - timedelta(days=lb - 1)
    start_dt = datetime(start_d.year, start_d.month, start_d.day)
    end_dt = datetime(end_d.year, end_d.month, end_d.day) + timedelta(days=1)
    cid = int(chat_id)

    try:
        with sync_session_scope() as session:
            qm = select(Message).where(
                Message.chat_id == cid,
                Message.sent_at >= start_dt,
                Message.sent_at < end_dt,
            )
            msgs = list(session.scalars(qm).all())
            qs = select(MarketingSignalEvent).where(
                MarketingSignalEvent.chat_id == cid,
                MarketingSignalEvent.occurred_at >= start_dt,
                MarketingSignalEvent.occurred_at < end_dt,
            )
            sigs = list(session.scalars(qs).all())
    except Exception as e:
        logger.warning("chat_daily_series db failed chat=%s: %s", chat_id, e)
        return []

    by_day: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"messages": 0, "toxic_messages": 0, "active_users": []}
    )
    active_per_day: dict[str, set[str]] = defaultdict(set)

    for m in msgs:
        d = m.sent_at.date().isoformat() if m.sent_at else ""
        if not d:
            continue
        by_day[d]["messages"] = int(by_day[d]["messages"]) + 1
        if m.user_id is not None:
            active_per_day[d].add(str(int(m.user_id)))
        ts = m.tone_score
        if ts is not None and float(ts) < -0.25:
            by_day[d]["toxic_messages"] = int(by_day[d]["toxic_messages"]) + 1

    for ev in sigs:
        d = ev.occurred_at.date().isoformat() if ev.occurred_at else ""
        if not d:
            continue
        if ev.sentiment == "negative":
            by_day[d]["toxic_messages"] = int(by_day[d]["toxic_messages"]) + 1

    for d, users in active_per_day.items():
        by_day[d]["active_users"] = sorted(users)

    rows = sorted(by_day.items(), key=lambda x: x[0])
    return rows


def get_user_metrics_from_db(
    *,
    user_id: int,
    chat_id: int | None,
    days: int,
    window_end_offset_days: int,
    graph_for_chat: Any,
) -> dict[str, Any] | None:
    """Собирает combined bucket; ``graph_for_chat(cid)`` — как ``_graph_lookup``."""
    start_dt, end_dt = _window_datetimes(days=days, end_offset_days=window_end_offset_days)
    end_anchor = _end_anchor_date(end_offset_days=window_end_offset_days)
    uid = int(user_id)
    safe_days = max(1, int(days))

    try:
        with sync_session_scope() as session:
            if chat_id is None:
                chat_ids = distinct_chats_for_user(session, uid, start_dt, end_dt)
            else:
                chat_ids = [int(chat_id)]
            display_name = _display_name(session, uid)
            combined = new_bucket()
            active_days: set[str] = set()
            latest_active = ""
            weighted_pagerank = 0.0
            weighted_reach = 0.0
            weighted_base = 0.0

            for cid in chat_ids:
                agg = aggregate_user_for_chat(session, user_id=uid, chat_id=cid, start_dt=start_dt, end_dt=end_dt)
                totals = agg["totals"]
                for k, v in totals.items():
                    combined[k] = combined.get(k, 0.0) + float(v or 0.0)
                active_days |= set(agg["active_days"])
                la = agg["last_active"]
                if la and (not latest_active or la > latest_active):
                    latest_active = la
                gtab: dict[int, dict] = {}
                try:
                    gtab = graph_for_chat(int(cid))  # type: ignore[misc]
                except Exception:
                    gtab = {}
                g_user = gtab.get(uid, {})
                weight = max(1.0, float(totals.get("messages", 0.0) or 0.0))
                weighted_base += weight
                weighted_pagerank += weight * float(g_user.get("pagerank", 0.0) or 0.0)
                weighted_reach += weight * float(g_user.get("reach", 0.0) or 0.0)
    except Exception as e:
        logger.warning("get_user_metrics_from_db failed: %s", e)
        return None

    return {
        "user_id": uid,
        "chat_id": "all" if chat_id is None else int(chat_id),
        "days": safe_days,
        "display_name": display_name,
        "combined": combined,
        "active_days": active_days,
        "latest_active": latest_active,
        "weighted_pagerank": weighted_pagerank,
        "weighted_reach": weighted_reach,
        "weighted_base": weighted_base,
        "end_anchor": end_anchor,
    }
