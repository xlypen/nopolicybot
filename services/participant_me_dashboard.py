"""Агрегаты для страницы /me: активность, темы, P-1, цитата, мосты графа, текст для копирования."""

from __future__ import annotations

import logging
import random
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy import desc as sql_desc

logger = logging.getLogger(__name__)


def participant_token_expiry_iso(token: str) -> tuple[str | None, int | None]:
    """
    Дата окончания токена /me (без повторной проверки подписи — вызывать только для уже проверенного токена).
    Возвращает (YYYY-MM-DD, секунд до истечения) или (None, None).
    """
    import base64

    if not token or "." not in token:
        return None, None
    try:
        payload_b64 = token.split(".", 1)[0]
        payload = base64.urlsafe_b64decode(payload_b64 + "==").decode("utf-8")
        parts = payload.split(":")
        if len(parts) != 2:
            return None, None
        exp = int(parts[1])
        dt = datetime.fromtimestamp(exp, tz=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        left = max(0, int(exp - int(now.timestamp())))
        return dt.strftime("%Y-%m-%d"), left
    except Exception:
        return None, None


def _count_messages_days(user_id: int, days: int) -> int:
    from db.models import Message
    from db.sync_engine import sync_session_scope

    if days < 1:
        return 0
    try:
        since = datetime.utcnow() - timedelta(days=days)
        with sync_session_scope() as session:
            n = session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.user_id == int(user_id))
                .where(Message.sent_at >= since)
            ).scalar_one()
        return int(n or 0)
    except Exception as e:
        logger.debug("_count_messages_days: %s", e)
        return 0


def _count_political_signals_days(user_id: int, days: int) -> int | None:
    from db.models import MarketingSignalEvent
    from db.sync_engine import sync_session_scope

    if days < 1:
        return None
    try:
        since = datetime.utcnow() - timedelta(days=days)
        with sync_session_scope() as session:
            n = session.execute(
                select(func.count())
                .select_from(MarketingSignalEvent)
                .where(MarketingSignalEvent.user_id == int(user_id))
                .where(MarketingSignalEvent.is_political.is_(True))
                .where(MarketingSignalEvent.occurred_at >= since)
            ).scalar_one()
        return int(n or 0)
    except Exception as e:
        logger.debug("_count_political_signals_days: %s", e)
        return None


def aggregate_topics_and_tones_from_connections(rows: list[dict]) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    topics: Counter[str] = Counter()
    tones: Counter[str] = Counter()
    for r in rows or []:
        t = str(r.get("tone") or "neutral").strip().lower() or "neutral"
        tones[t] += max(1, int(r.get("message_count_7d", 0) or 0) // 2 + 1)
        for topic in r.get("topics") or []:
            tt = str(topic).strip()
            if tt:
                topics[tt] += 1
    top_t = topics.most_common(5)
    top_tone = tones.most_common(5)
    return top_t, top_tone


def personality_drift_line(user_id: int) -> str | None:
    """Краткая строка о сдвиге OCEAN между двумя последними профилями P-1 (любой чат)."""
    from db.models import PersonalityProfileRow
    from db.sync_engine import sync_session_scope

    try:
        with sync_session_scope() as session:
            stmt = (
                select(PersonalityProfileRow)
                .where(PersonalityProfileRow.user_id == int(user_id))
                .order_by(sql_desc(PersonalityProfileRow.generated_at))
                .limit(2)
            )
            rows = list(session.execute(stmt).scalars().all())
        if len(rows) < 2:
            return None
        new_j = rows[0].profile_json if isinstance(rows[0].profile_json, dict) else {}
        old_j = rows[1].profile_json if isinstance(rows[1].profile_json, dict) else {}
        ocean_n = (new_j.get("ocean") or {}) if isinstance(new_j.get("ocean"), dict) else {}
        ocean_o = (old_j.get("ocean") or {}) if isinstance(old_j.get("ocean"), dict) else {}
        if not ocean_n or not ocean_o:
            return None
        keys = ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]
        deltas: list[tuple[str, float]] = []
        for k in keys:
            try:
                a = float(ocean_o.get(k, 0.5))
                b = float(ocean_n.get(k, 0.5))
                deltas.append((k, b - a))
            except (TypeError, ValueError):
                continue
        if not deltas:
            return None
        deltas.sort(key=lambda x: abs(x[1]), reverse=True)
        k, d = deltas[0]
        ru = {
            "openness": "открытость",
            "conscientiousness": "добросовестность",
            "extraversion": "экстраверсия",
            "agreeableness": "доброжелательность",
            "neuroticism": "нейротизм",
        }.get(k, k)
        if abs(d) < 0.04:
            return "По сравнению с прошлым расчётом профиль личности почти не сместился."
        direction = "выше" if d > 0 else "ниже"
        return f"За время между расчётами заметнее всего изменилась «{ru}»: стала {direction} на {abs(d):.2f} по шкале 0–1."
    except Exception as e:
        logger.debug("personality_drift_line: %s", e)
        return None


def random_quote_from_archive(user_id: int, max_len: int = 180) -> str | None:
    import user_stats

    try:
        msgs = user_stats.get_user_messages_archive(int(user_id), None)
        if not msgs:
            return None
        pool = [str(m.get("text") or "").strip() for m in msgs if len(str(m.get("text") or "").strip()) > 25]
        if not pool:
            return None
        q = random.choice(pool)
        q = re.sub(r"\s+", " ", q)
        if len(q) > max_len:
            q = q[: max_len - 1].rsplit(" ", 1)[0] + "…"
        return q
    except Exception as e:
        logger.debug("random_quote_from_archive: %s", e)
        return None


def bridge_neighbors_from_graph(graph: dict, ego_id: int, limit: int = 5) -> list[dict[str, Any]]:
    """Рёбра с ненулевым bridge_score, инцидентные ego."""
    ego = int(ego_id)
    edges = graph.get("edges") or []
    nodes = {int(n.get("id", 0) or 0): n for n in (graph.get("nodes") or []) if int(n.get("id", 0) or 0)}
    scored: list[tuple[float, int, dict]] = []
    for e in edges:
        bs = float(e.get("bridge_score", 0) or 0)
        if bs <= 0:
            continue
        s = int(e.get("source", 0) or 0)
        t = int(e.get("target", 0) or 0)
        if s == ego:
            peer = t
        elif t == ego:
            peer = s
        else:
            continue
        if not peer:
            continue
        scored.append((bs, peer, e))
    scored.sort(key=lambda x: -x[0])
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for bs, peer, _e in scored:
        if peer in seen:
            continue
        seen.add(peer)
        label = (nodes.get(peer) or {}).get("label") or str(peer)
        out.append({"peer_id": peer, "peer_name": str(label), "bridge_score": round(bs, 4)})
        if len(out) >= max(1, int(limit)):
            break
    return out


def build_badges(
    u: dict[str, Any],
    portrait_exists: bool,
    msgs_7d: int,
    msgs_30d: int,
    similarity_ok: bool,
) -> list[str]:
    badges: list[str] = []
    tm = int((u.get("stats") or {}).get("total_messages", 0) or 0)
    if tm >= 500:
        badges.append("500+ сообщений в учёте")
    elif tm >= 100:
        badges.append("100+ сообщений в учёте")
    if portrait_exists:
        badges.append("Есть визуальный портрет")
    if (u.get("portrait") or "").strip():
        badges.append("Текстовый портрет заполнен")
    if msgs_7d >= 10:
        badges.append("Активная неделя в чатах")
    elif msgs_30d >= 30:
        badges.append("Стабильная активность за месяц")
    if similarity_ok:
        badges.append("Сравнение с участниками доступно")
    return badges[:8]


def build_plain_summary(
    display_name: str,
    user_id: int,
    u: dict[str, Any],
    effective_tone: str,
    msgs_7d: int,
    msgs_30d: int,
    pol_7d: int | None,
    top_topics: list[tuple[str, int]],
    top_tones: list[tuple[str, int]],
    similarity_peers: dict[str, Any],
    token_expires: str | None,
    quote: str | None,
    drift: str | None,
) -> str:
    lines = [
        f"Краткое резюме профиля: {display_name} (id {user_id})",
        f"Полит. позиция: {u.get('rank', 'unknown')}; настроение (к боту): {effective_tone or '—'}",
        f"Сообщений за 7 дн.: {msgs_7d}; за 30 дн.: {msgs_30d}",
    ]
    if pol_7d is not None:
        lines.append(f"Политических сигналов (ИИ) за 7 дн.: {pol_7d}")
    if top_tones:
        lines.append("Тоны в диалогах (вес по связям): " + ", ".join(f"{t} ({c})" for t, c in top_tones[:3]))
    if top_topics:
        lines.append("Частые темы в связях: " + ", ".join(f"{t} ({c})" for t, c in top_topics[:5]))
    if drift:
        lines.append("Личность (P-1): " + drift)
    if quote:
        lines.append('Случайная цитата из вашего архива: "' + quote + '"')
    if similarity_peers.get("ok") and similarity_peers.get("most"):
        m = similarity_peers["most"]
        lines.append(f"Наиболее похожий участник: {m.get('peer_name')} (чат {m.get('chat_id')}, ~{int(float(m.get('score', 0)) * 100)}%)")
    if similarity_peers.get("ok") and similarity_peers.get("least"):
        l = similarity_peers["least"]
        lines.append(f"Наименее похожий: {l.get('peer_name')} (чат {l.get('chat_id')}, ~{int(float(l.get('score', 0)) * 100)}%)")
    if token_expires:
        lines.append(f"Ссылка на эту страницу действует примерно до {token_expires} (UTC).")
    lines.append("— Сгенерировано на странице «Мой профиль» бота.")
    return "\n".join(lines)


def build_me_dashboard(
    user_id: int,
    token: str,
    u: dict[str, Any],
    effective_tone: str,
    my_connections: list[dict],
    similarity_peers: dict[str, Any],
    portrait_exists: bool,
    graph_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Единый словарь для шаблона participant_me (секции обзора, активности и т.д.)."""
    exp_date, exp_left = participant_token_expiry_iso(token)
    msgs_7d = _count_messages_days(user_id, 7)
    msgs_30d = _count_messages_days(user_id, 30)
    pol_7d = _count_political_signals_days(user_id, 7)
    top_topics, top_tones = aggregate_topics_and_tones_from_connections(my_connections)
    drift = personality_drift_line(user_id)
    quote = random_quote_from_archive(user_id)
    bridges = bridge_neighbors_from_graph(graph_payload or {}, int(user_id), 5) if graph_payload else []

    qod_enabled = bool(u.get("question_of_day_enabled"))
    qod_dest = str(u.get("question_of_day_destination") or "chat")
    qod_last = str(u.get("question_of_day_last_asked") or "").strip() or "—"

    badges = build_badges(u, portrait_exists, msgs_7d, msgs_30d, bool(similarity_peers.get("ok")))

    display_name = str(u.get("display_name") or user_id)
    summary_plain = build_plain_summary(
        display_name,
        int(user_id),
        u,
        effective_tone,
        msgs_7d,
        msgs_30d,
        pol_7d,
        top_topics,
        top_tones,
        similarity_peers,
        exp_date,
        quote,
        drift,
    )

    return {
        "token_expires_date": exp_date,
        "token_expires_seconds": exp_left,
        "msgs_7d": msgs_7d,
        "msgs_30d": msgs_30d,
        "political_signals_7d": pol_7d,
        "top_topics": top_topics[:5],
        "top_tones": top_tones[:5],
        "personality_drift": drift,
        "quote": quote,
        "bridges": bridges,
        "qod_enabled": qod_enabled,
        "qod_destination": qod_dest,
        "qod_destination_ru": {"chat": "в чат", "private": "в личку"}.get(qod_dest, qod_dest),
        "qod_last_asked": qod_last,
        "badges": badges,
        "summary_plain": summary_plain,
    }
