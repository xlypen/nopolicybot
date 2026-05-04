"""
Ответы на вопросы по аналитике чата.

Без ИИ: ключевые слова → параметризованный SQL → шаблонный текст.

С ИИ (use_llm=True): один вызов LLM с компактным JSON агрегатов (без текста
сообщений), чтобы не перегружать запрос.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from db.sync_engine import sync_session_scope

logger = logging.getLogger(__name__)

_WEEKDAYS_RU = ("вс", "пн", "вт", "ср", "чт", "пт", "сб")


def infer_period_days(question: str, default: int = 30) -> int:
    q = question.lower()
    if re.search(r"\bза\s+год\b", q) or re.search(r"\bза\s+12\s*мес", q):
        return 365
    if re.search(r"\bза\s+(месяц|30\s*дн)", q):
        return 30
    if re.search(r"\bза\s+неделю\b", q):
        return 7
    if re.search(r"\bза\s+сутки\b|\bза\s+день\b|\bсегодня\b|\bвчера\b|\bза\s+24\s*ч", q):
        return 1
    m = re.search(r"за\s+(\d+)\s*дн", q)
    if m:
        return max(1, min(366, int(m.group(1))))
    return default


_INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "tone_distribution",
        re.compile(
            r"позитив|негатив|нейтрал|распределен|распределён|долей|доля\s+позитив|tone\s+distribution",
            re.I,
        ),
    ),
    (
        "author_tone",
        re.compile(
            r"весел|весёлы|юмор|шутк|смешн|funniest|кто\s+самый\s+позитив|позитивн.*автор|оптимист|"
            r"добрейш|настроен.*участник",
            re.I,
        ),
    ),
    ("tone_avg", re.compile(r"средн.*тон|настроен|средн.*тональн|avg\s+tone|sentiment", re.I)),
    (
        "peak_time",
        re.compile(
            r"когда\s+активн|активнее\s+всего|время\s+суток|час\s+пик|во\s+сколько|"
            r"пик\s+активн|heatmap|в\s+какое\s+время",
            re.I,
        ),
    ),
    (
        "active_users",
        re.compile(
            r"участник|пользовател|активн(?:ых|ые|ый)?\s+(?:автор|участник|юзер|пользов)|"
            r"уникальн|сколько\s+людей|сколько\s+юзер|members",
            re.I,
        ),
    ),
    (
        "message_count",
        re.compile(
            r"сколько\s+сообщ|число\s+сообщ|объём\s+сообщ|объем\s+сообщ|count\s+messages|message\s+count",
            re.I,
        ),
    ),
    ("reply_rate", re.compile(r"ответ|репла|reply|тред|отвечают\s+сообщ", re.I)),
    (
        "top_posters",
        re.compile(
            r"кто\s+больше\s+пишет|топ\s+активн|самый\s+активн|лидер|больше\s+всех\s+сообщ|топ\s+автор",
            re.I,
        ),
    ),
    (
        "reply_pairs",
        re.compile(r"кому\s+отвечают|пары\s+ответ|кто\s+к\s+кому|диалог\s+между|топ\s+пар", re.I),
    ),
    ("edges", re.compile(r"связ|рёбра|ребра|\bedges\b|граф\s+связ", re.I)),
    ("political", re.compile(r"политик|политическ", re.I)),
    ("overview", re.compile(r"общ|кратк|сводк|итог|резюме|overview|summary|picture", re.I)),
]


_FALLBACK_OVERVIEW_RX = re.compile(
    r"чат|данн|стат|сколько|кто|какой|когда|где|тон|сообщ|актив|ответ|участник|недел|месяц|сводк|"
    r"chart|stats|how\s+many|who|when|messages|tone|active|summary",
    re.I,
)


def detect_intents(question: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for intent, rx in _INTENT_PATTERNS:
        if rx.search(question) and intent not in seen:
            seen.add(intent)
            ordered.append(intent)
    return ordered


def _strip_overview_duplicates(intents: list[str]) -> list[str]:
    """Обзор уже включает объём/тон/ответы — убираем дубли если есть overview."""
    if "overview" not in intents:
        return intents
    drop = {"message_count", "tone_avg", "reply_rate", "active_users"}
    return ["overview"] + [i for i in intents if i not in drop and i != "overview"]


def _scope_sql(chat_id: int | None) -> tuple[str, dict[str, Any]]:
    if chat_id is None:
        return "", {}
    return " AND m.chat_id = :qa_chat_id", {"qa_chat_id": int(chat_id)}


def _scope_edges(chat_id: int | None) -> tuple[str, dict[str, Any]]:
    if chat_id is None:
        return "", {}
    return " AND chat_id = :qa_chat_id", {"qa_chat_id": int(chat_id)}


def _since_param(period_days: int) -> datetime:
    utc = datetime.now(timezone.utc) - timedelta(days=max(1, int(period_days)))
    return utc.replace(tzinfo=None)


def _run_overview(
    session,
    is_pg: bool,
    since: datetime,
    chat_scope: str,
    scope_params: dict[str, Any],
) -> dict[str, Any]:
    params = {"qa_since": since, **scope_params}
    base_msgs = f"FROM messages m WHERE sent_at IS NOT NULL AND sent_at >= :qa_since{chat_scope}"
    total = int(session.execute(text(f"SELECT COUNT(*) {base_msgs}"), params).scalar() or 0)
    users = int(
        session.execute(
            text(
                f"SELECT COUNT(DISTINCT m.user_id) {base_msgs} AND m.user_id IS NOT NULL"
            ),
            params,
        ).scalar()
        or 0
    )
    row = session.execute(
        text(
            f"SELECT COUNT(*), AVG(m.tone_score) {base_msgs} AND m.tone_score IS NOT NULL"
        ),
        params,
    ).fetchone()
    scored = int(row[0] or 0)
    avg_tone = float(row[1]) if row[1] is not None else None
    replies = int(
        session.execute(
            text(f"SELECT COUNT(*) {base_msgs} AND m.replied_to IS NOT NULL"),
            params,
        ).scalar()
        or 0
    )
    rr = int(round(100.0 * replies / total)) if total else 0
    return {
        "period_days_used": None,
        "messages": total,
        "active_users": users,
        "scored": scored,
        "avg_tone": avg_tone,
        "reply_rate_pct": rr,
    }


def _run_tone_distribution(
    session,
    is_pg: bool,
    since: datetime,
    chat_scope: str,
    scope_params: dict[str, Any],
) -> dict[str, Any]:
    params = {"qa_since": since, **scope_params}
    base = f"""FROM messages m
        WHERE m.sent_at IS NOT NULL AND m.sent_at >= :qa_since
          AND m.tone_score IS NOT NULL{chat_scope}"""
    neg = int(
        session.execute(text(f"SELECT COUNT(*) {base} AND m.tone_score < -0.3"), params).scalar()
        or 0
    )
    neu = int(
        session.execute(
            text(f"SELECT COUNT(*) {base} AND m.tone_score BETWEEN -0.3 AND 0.3"),
            params,
        ).scalar()
        or 0
    )
    pos = int(
        session.execute(text(f"SELECT COUNT(*) {base} AND m.tone_score > 0.3"), params).scalar()
        or 0
    )
    total = neg + neu + pos
    return {"tone_buckets": {"negative": neg, "neutral": neu, "positive": pos}, "tone_total": total}


def _run_message_count(
    session,
    since: datetime,
    chat_scope: str,
    scope_params: dict[str, Any],
) -> dict[str, Any]:
    params = {"qa_since": since, **scope_params}
    base = f"FROM messages m WHERE sent_at IS NOT NULL AND sent_at >= :qa_since{chat_scope}"
    n = int(session.execute(text(f"SELECT COUNT(*) {base}"), params).scalar() or 0)
    return {"messages": n}


def _run_active_users(
    session,
    since: datetime,
    chat_scope: str,
    scope_params: dict[str, Any],
) -> dict[str, Any]:
    params = {"qa_since": since, **scope_params}
    base = f"FROM messages m WHERE sent_at IS NOT NULL AND sent_at >= :qa_since AND m.user_id IS NOT NULL{chat_scope}"
    n = int(session.execute(text(f"SELECT COUNT(DISTINCT m.user_id) {base}"), params).scalar() or 0)
    return {"active_users": n}


def _run_tone_avg(
    session,
    since: datetime,
    chat_scope: str,
    scope_params: dict[str, Any],
) -> dict[str, Any]:
    params = {"qa_since": since, **scope_params}
    base = f"FROM messages m WHERE sent_at IS NOT NULL AND sent_at >= :qa_since AND m.tone_score IS NOT NULL{chat_scope}"
    row = session.execute(text(f"SELECT COUNT(*), AVG(m.tone_score) {base}"), params).fetchone()
    return {"scored": int(row[0] or 0), "avg_tone": float(row[1]) if row[1] is not None else None}


def _run_reply_rate(
    session,
    since: datetime,
    chat_scope: str,
    scope_params: dict[str, Any],
) -> dict[str, Any]:
    params = {"qa_since": since, **scope_params}
    base = f"FROM messages m WHERE sent_at IS NOT NULL AND sent_at >= :qa_since{chat_scope}"
    total = int(session.execute(text(f"SELECT COUNT(*) {base}"), params).scalar() or 0)
    repl = int(
        session.execute(text(f"SELECT COUNT(*) {base} AND m.replied_to IS NOT NULL"), params).scalar()
        or 0
    )
    pct = int(round(100.0 * repl / total)) if total else 0
    return {"messages": total, "with_reply": repl, "reply_rate_pct": pct}


def _run_top_posters(
    session,
    is_pg: bool,
    since: datetime,
    chat_scope: str,
    scope_params: dict[str, Any],
    *,
    limit: int = 8,
):
    lim = max(2, min(20, int(limit)))
    params = {"qa_since": since, "qa_lim": lim, **scope_params}
    if is_pg:
        sql = f"""
            SELECT COALESCE(
                NULLIF(TRIM(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '')), ''),
                u.username,
                'User ' || u.id::text
              ) AS display_name,
              COUNT(*)::int AS cnt
            FROM messages m
            LEFT JOIN users u ON m.user_id = u.id
            WHERE m.sent_at IS NOT NULL AND m.sent_at >= :qa_since{chat_scope}
            GROUP BY m.user_id, u.first_name, u.last_name, u.username, u.id
            ORDER BY cnt DESC
            LIMIT :qa_lim
            """
    else:
        sql = f"""
            SELECT COALESCE(
                NULLIF(TRIM(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '')), ''),
                u.username,
                'User ' || CAST(u.id AS TEXT)
              ) AS display_name,
              COUNT(*) AS cnt
            FROM messages m
            LEFT JOIN users u ON m.user_id = u.id
            WHERE m.sent_at IS NOT NULL AND m.sent_at >= :qa_since{chat_scope}
            GROUP BY m.user_id
            ORDER BY cnt DESC
            LIMIT :qa_lim
            """
    rows = session.execute(text(sql), params).fetchall()
    return [{"name": str(r[0] or "?"), "count": int(r[1] or 0)} for r in rows]


def _run_peak_time(session, is_pg: bool, since: datetime, chat_scope: str, scope_params: dict[str, Any]):
    params = {"qa_since": since, **scope_params}
    if is_pg:
        sql = f"""
            SELECT EXTRACT(DOW FROM m.sent_at AT TIME ZONE 'UTC')::int AS dow,
                   EXTRACT(HOUR FROM m.sent_at AT TIME ZONE 'UTC')::int AS hr,
                   COUNT(*)::int AS c
            FROM messages m
            WHERE m.sent_at IS NOT NULL AND m.sent_at >= :qa_since{chat_scope}
            GROUP BY 1, 2
            ORDER BY c DESC
            LIMIT 1
            """
    else:
        sql = f"""
            SELECT CAST(strftime('%w', m.sent_at) AS INTEGER) AS dow,
                   CAST(strftime('%H', m.sent_at) AS INTEGER) AS hr,
                   COUNT(*) AS c
            FROM messages m
            WHERE m.sent_at IS NOT NULL AND m.sent_at >= :qa_since{chat_scope}
            GROUP BY 1, 2
            ORDER BY c DESC
            LIMIT 1
            """
    row = session.execute(text(sql), params).fetchone()
    if not row:
        return None
    return {"dow": int(row[0]) % 7, "hour": int(row[1]) % 24, "count": int(row[2] or 0)}


def _run_reply_pairs(
    session,
    is_pg: bool,
    since: datetime,
    chat_scope: str,
    scope_params: dict[str, Any],
    *,
    limit: int = 8,
):
    lim = max(2, min(20, int(limit)))
    params = {"qa_since": since, "qa_lim": lim, **scope_params}
    if is_pg:
        sql = f"""
            SELECT
              COALESCE(uf.first_name, 'User ' || e.from_user::text),
              COALESCE(ut.first_name, 'User ' || e.to_user::text),
              e.cnt::int
            FROM (
              SELECT m.user_id AS from_user, m.replied_to AS to_user, COUNT(*)::int AS cnt
              FROM messages m
              WHERE m.replied_to IS NOT NULL AND m.sent_at IS NOT NULL AND m.sent_at >= :qa_since{chat_scope}
              GROUP BY m.user_id, m.replied_to
            ) e
            LEFT JOIN users uf ON uf.id = e.from_user
            LEFT JOIN users ut ON ut.id = e.to_user
            ORDER BY e.cnt DESC
            LIMIT :qa_lim
            """
    else:
        sql = f"""
            SELECT
              COALESCE(uf.first_name, 'User ' || CAST(e.from_user AS TEXT)),
              COALESCE(ut.first_name, 'User ' || CAST(e.to_user AS TEXT)),
              e.cnt
            FROM (
              SELECT m.user_id AS from_user, m.replied_to AS to_user, COUNT(*) AS cnt
              FROM messages m
              WHERE m.replied_to IS NOT NULL AND m.sent_at IS NOT NULL AND m.sent_at >= :qa_since{chat_scope}
              GROUP BY m.user_id, m.replied_to
            ) e
            LEFT JOIN users uf ON uf.id = e.from_user
            LEFT JOIN users ut ON ut.id = e.to_user
            ORDER BY e.cnt DESC
            LIMIT :qa_lim
            """
    rows = session.execute(text(sql), params).fetchall()
    return [{"from": str(r[0] or "?"), "to": str(r[1] or "?"), "count": int(r[2] or 0)} for r in rows]


def _run_edges_count(session, scope_edges: str, scope_params: dict[str, Any]) -> dict[str, Any]:
    params = dict(scope_params)
    sql = f"SELECT COUNT(*) FROM edges WHERE 1=1 {scope_edges}"
    n = int(session.execute(text(sql), params).scalar() or 0)
    return {"edges": n}


def _run_political_count(session, is_pg: bool, since: datetime, chat_id: int | None) -> dict[str, Any]:
    params = {"qa_since": since}
    if chat_id is not None:
        params["qa_chat_id"] = int(chat_id)
        wc = " AND chat_id = :qa_chat_id"
    else:
        wc = ""
    if is_pg:
        sql = f"""
            SELECT COUNT(*) FROM marketing_signal_events
            WHERE occurred_at >= :qa_since AND is_political IS TRUE{wc}
            """
    else:
        sql = f"""
            SELECT COUNT(*) FROM marketing_signal_events
            WHERE occurred_at >= :qa_since AND is_political = 1{wc}
            """
    n = int(session.execute(text(sql), params).scalar() or 0)
    return {"political_events": n}


def _author_tone_min_msgs() -> int:
    return max(2, min(50, int(os.getenv("ANALYTICS_QA_AUTHOR_TONE_MIN_MSGS", "3"))))


def _run_author_tone_breakdown(
    session,
    is_pg: bool,
    since: datetime,
    chat_scope: str,
    scope_params: dict[str, Any],
    *,
    pool_limit: int = 48,
) -> list[dict[str, Any]]:
    """По авторам за период: сколько сообщений с tone_score, средний тон, позитив/негатив по порогам."""
    min_msgs = _author_tone_min_msgs()
    lim = max(12, min(80, int(pool_limit)))
    params = {
        "qa_since": since,
        "qa_min_msgs": min_msgs,
        "qa_pool": lim,
        **scope_params,
    }
    if is_pg:
        sql = f"""
            SELECT COALESCE(
                NULLIF(TRIM(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '')), ''),
                u.username,
                'User ' || u.id::text
              ) AS display_name,
              COUNT(*)::int AS n_scored,
              AVG(m.tone_score)::double precision AS avg_t,
              SUM(CASE WHEN m.tone_score > 0.3 THEN 1 ELSE 0 END)::int AS pos_n,
              SUM(CASE WHEN m.tone_score < -0.3 THEN 1 ELSE 0 END)::int AS neg_n
            FROM messages m
            LEFT JOIN users u ON m.user_id = u.id
            WHERE m.sent_at IS NOT NULL AND m.sent_at >= :qa_since
              AND m.tone_score IS NOT NULL
              AND m.user_id IS NOT NULL
              {chat_scope}
            GROUP BY m.user_id, u.first_name, u.last_name, u.username, u.id
            HAVING COUNT(*) >= :qa_min_msgs
            ORDER BY COUNT(*) DESC
            LIMIT :qa_pool
            """
    else:
        sql = f"""
            SELECT COALESCE(
                NULLIF(TRIM(COALESCE(u.first_name, '') || ' ' || COALESCE(u.last_name, '')), ''),
                u.username,
                'User ' || CAST(u.id AS TEXT)
              ) AS display_name,
              COUNT(*) AS n_scored,
              AVG(m.tone_score) AS avg_t,
              SUM(CASE WHEN m.tone_score > 0.3 THEN 1 ELSE 0 END) AS pos_n,
              SUM(CASE WHEN m.tone_score < -0.3 THEN 1 ELSE 0 END) AS neg_n
            FROM messages m
            LEFT JOIN users u ON m.user_id = u.id
            WHERE m.sent_at IS NOT NULL AND m.sent_at >= :qa_since
              AND m.tone_score IS NOT NULL
              AND m.user_id IS NOT NULL
              {chat_scope}
            GROUP BY m.user_id
            ORDER BY COUNT(*) DESC
            LIMIT :qa_pool
            """
    rows = session.execute(text(sql), params).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        avg_raw = r[2]
        avg_tone = float(avg_raw) if avg_raw is not None else None
        out.append(
            {
                "name": str(r[0] or "?"),
                "scored_msgs": int(r[1] or 0),
                "avg_tone": avg_tone,
                "positive_msgs": int(r[3] or 0),
                "negative_msgs": int(r[4] or 0),
            }
        )
    return out


_LLM_SYSTEM_PROMPT = """Ты помощник по аналитике Telegram-чата.
Даны только агрегированные метрики в JSON (счётчики, средние, топы имён и связей).
Текста сообщений пользователей нет — не выдумывай цитаты и конкретные фразы из чата.

Поля authors_highest_avg_tone и authors_most_positive_msgs — это статистика по уже размеченному
tone_score сообщений (−1…1): средний тон и число «позитивных» (score > 0.3) на автора.
Это прокси доброжелательности/позитива в тексте, не юмор и не реакции Telegram.

Ответь на вопрос по-русски кратко (обычно 3–7 предложений).
Опирайся только на числа из JSON; если данных недостаточно — так и скажи.
День недели с индексом 0 = воскресенье; часы в UTC."""


def _truncate_label(val: str, max_chars: int = 56) -> str:
    s = str(val or "").strip().replace("\n", " ")
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 1].rstrip() + "…"


def _collect_full_snapshot(
    session,
    is_pg: bool,
    since: datetime,
    chat_sql: str,
    chat_params: dict[str, Any],
    edge_sql: str,
    edge_params: dict[str, Any],
    chat_id: int | None,
    period_days: int,
) -> tuple[dict[str, Any], bool]:
    blocks: dict[str, Any] = {}
    partial = False
    sections = [
        ("overview", lambda: _run_overview(session, is_pg, since, chat_sql, chat_params)),
        (
            "tone_distribution",
            lambda: _run_tone_distribution(session, is_pg, since, chat_sql, chat_params),
        ),
        (
            "top_posters",
            lambda: _run_top_posters(session, is_pg, since, chat_sql, chat_params, limit=10),
        ),
        (
            "author_tone_breakdown",
            lambda: _run_author_tone_breakdown(session, is_pg, since, chat_sql, chat_params),
        ),
        ("peak_time", lambda: _run_peak_time(session, is_pg, since, chat_sql, chat_params)),
        (
            "reply_pairs",
            lambda: _run_reply_pairs(session, is_pg, since, chat_sql, chat_params, limit=8),
        ),
        ("edges", lambda: _run_edges_count(session, edge_sql, edge_params)),
        ("political", lambda: _run_political_count(session, is_pg, since, chat_id)),
    ]
    for key, fn in sections:
        try:
            blocks[key] = fn()
        except Exception:
            logger.warning("analytics snapshot section=%s failed", key, exc_info=True)
            partial = True
    if isinstance(blocks.get("overview"), dict):
        blocks["overview"]["period_days_used"] = period_days
    return blocks, partial


def _snapshot_for_llm_payload(
    blocks: dict[str, Any], *, period_days: int, chat_id: int | None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "period_days": period_days,
        "chat_id": chat_id,
        "time_basis": "UTC",
    }
    ov = blocks.get("overview")
    if isinstance(ov, dict):
        out["messages"] = ov.get("messages")
        out["distinct_authors"] = ov.get("active_users")
        out["tone_scored_messages"] = ov.get("scored")
        out["avg_tone"] = ov.get("avg_tone")
        out["reply_rate_percent"] = ov.get("reply_rate_pct")

    td = blocks.get("tone_distribution")
    if isinstance(td, dict) and td.get("tone_total"):
        out["tone_buckets"] = td.get("tone_buckets")

    tp = blocks.get("top_posters")
    if isinstance(tp, list):
        out["top_posters"] = [
            {"name": _truncate_label(x.get("name", "?")), "count": x.get("count")} for x in tp[:10]
        ]

    rows = blocks.get("author_tone_breakdown")
    if isinstance(rows, list) and rows:
        pool = []
        for x in rows:
            at = x.get("avg_tone")
            pool.append(
                {
                    "name": _truncate_label(x.get("name", "?")),
                    "scored_msgs": x.get("scored_msgs"),
                    "avg_tone": round(float(at), 4) if at is not None else None,
                    "positive_msgs": x.get("positive_msgs"),
                    "negative_msgs": x.get("negative_msgs"),
                }
            )
        by_avg = sorted(pool, key=lambda z: (-(z["avg_tone"] or 0.0), -int(z["scored_msgs"] or 0)))[:8]
        by_pos = sorted(
            pool,
            key=lambda z: (-int(z["positive_msgs"] or 0), -(z["avg_tone"] or 0.0)),
        )[:8]
        out["authors_highest_avg_tone"] = by_avg
        out["authors_most_positive_msgs"] = by_pos
        out["tone_semantics_note"] = (
            "tone_score per message −1…1; «positive_msgs» = число сообщений с tone_score>0.3. "
            "Не реакции и не детектор юмора."
        )

    pk = blocks.get("peak_time")
    if pk:
        dow = int(pk["dow"]) % 7
        out["peak_activity"] = {
            "weekday_index_0_sun": dow,
            "weekday_ru": _WEEKDAYS_RU[dow],
            "hour_utc": pk["hour"],
            "messages_at_cell": pk["count"],
        }

    rp = blocks.get("reply_pairs")
    if isinstance(rp, list):
        out["reply_pairs_top"] = [
            {
                "from": _truncate_label(x.get("from", "?")),
                "to": _truncate_label(x.get("to", "?")),
                "count": x.get("count"),
            }
            for x in rp[:8]
        ]

    eg = blocks.get("edges")
    if isinstance(eg, dict):
        out["edges_rows"] = eg.get("edges")

    pol = blocks.get("political")
    if isinstance(pol, dict):
        out["political_signals"] = pol.get("political_events")

    return out


def _answer_with_llm(question: str, snapshot: dict[str, Any]) -> tuple[str, str]:
    from ai.client import chat_complete_with_fallback, load_project_env, prefer_free_mode

    load_project_env()
    max_tokens = max(128, min(2048, int(os.getenv("ANALYTICS_QA_LLM_MAX_TOKENS", "512"))))
    model = (os.getenv("ANALYTICS_QA_LLM_MODEL") or "").strip() or None
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    if len(payload) > 12000:
        payload = payload[:11900] + "…[truncated]"

    messages = [
        {"role": "system", "content": _LLM_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Вопрос: {question.strip()}\n\nДанные (JSON):\n{payload}",
        },
    ]
    raw, used = chat_complete_with_fallback(
        messages,
        model=model,
        max_tokens=max_tokens,
        temperature=0.25,
        prefer_free=prefer_free_mode(),
    )
    text = (raw or "").strip()
    if not text:
        return (
            "ИИ не вернул ответ (проверьте ключи API в .env). Ниже агрегаты:\n"
            + json.dumps(snapshot, ensure_ascii=False, indent=2)[:4000],
            used or "none",
        )
    return text, used


def _chat_label(chat_id: int | None) -> str:
    if chat_id is None:
        return "по всем чатам в базе"
    return f"в чате {chat_id}"


def _render_sections(
    intents: list[str],
    blocks: dict[str, Any],
    *,
    period_days: int,
    chat_id: int | None,
) -> str:
    parts: list[str] = []
    scope = _chat_label(chat_id)
    parts.append(f"За последние {period_days} дн. ({scope}), из сохранённых метрик:")

    if "overview" in blocks:
        o = blocks["overview"]
        at = o.get("avg_tone")
        at_s = f"{at:.3f}" if isinstance(at, float) else "—"
        parts.append(
            f"• Сообщений: {o['messages']:,}; активных авторов: {o['active_users']}; "
            f"с разметкой тона: {o['scored']:,}; средний тон: {at_s}; "
            f"доля с ответом (reply): {o['reply_rate_pct']} %."
        )

    if "tone_distribution" in blocks:
        t = blocks["tone_distribution"]
        tot = t.get("tone_total") or 0
        if tot == 0:
            parts.append("• Распределение тона: нет размеченных сообщений за период.")
        else:
            b = t["tone_buckets"]
            parts.append(
                f"• Тон (размеченные): негатив {b['negative']} ({100*b['negative']/tot:.0f} %), "
                f"нейтральные {b['neutral']} ({100*b['neutral']/tot:.0f} %), "
                f"позитив {b['positive']} ({100*b['positive']/tot:.0f} %)."
            )

    if "message_count" in blocks:
        parts.append(f"• Сообщений за период: {blocks['message_count']['messages']:,}.")

    if "active_users" in blocks:
        parts.append(f"• Уникальных авторов: {blocks['active_users']['active_users']:,}.")

    if "tone_avg" in blocks:
        ta = blocks["tone_avg"]
        avg = ta.get("avg_tone")
        avg_s = f"{avg:.3f}" if isinstance(avg, float) else "—"
        parts.append(f"• По размеченным ({ta['scored']} шт.) средний тон: {avg_s}.")

    if "reply_rate" in blocks:
        rr = blocks["reply_rate"]
        parts.append(
            f"• Ответы: {rr['with_reply']:,} из {rr['messages']:,} сообщений ({rr['reply_rate_pct']} %)."
        )

    if "top_posters" in blocks:
        lines = [f"  — {x['name']}: {x['count']:,}" for x in blocks["top_posters"][:6]]
        parts.append("• Топ авторов:\n" + "\n".join(lines))

    if "peak_time" in blocks:
        pk = blocks["peak_time"]
        if pk:
            wd = _WEEKDAYS_RU[pk["dow"]]
            parts.append(
                f"• Пик активности: {wd}, {pk['hour']:02d}:00–{pk['hour']:02d}:59 UTC (~{pk['count']:,} сообщ.)."
            )
        else:
            parts.append("• Пик активности: данных за период нет.")

    if "reply_pairs" in blocks:
        lines = [f"  — {x['from']} → {x['to']}: {x['count']:,}" for x in blocks["reply_pairs"][:6]]
        parts.append("• Чаще всего отвечают (по счётчику пар):\n" + "\n".join(lines))

    if "author_tone_breakdown" in blocks:
        rows = blocks["author_tone_breakdown"]
        if not rows:
            parts.append(
                "• Тон по авторам: нет пользователей с достаточным числом размеченных сообщений "
                f"(минимум {_author_tone_min_msgs()} за период). Запустите fill_tone_scores.py для бэкфилла."
            )
        else:
            by_avg = sorted(
                rows,
                key=lambda x: (-(x.get("avg_tone") or 0.0), -int(x.get("scored_msgs") or 0)),
            )[:6]
            by_pos = sorted(
                rows,
                key=lambda x: (-int(x.get("positive_msgs") or 0), -(x.get("avg_tone") or 0.0)),
            )[:6]
            lines_a = [
                f"  — {x['name']}: средн.тон {x.get('avg_tone') if x.get('avg_tone') is not None else '—'}, "
                f"размечено {x['scored_msgs']}, позитив>{x['positive_msgs']}, негатив<{x['negative_msgs']}"
                for x in by_avg
            ]
            lines_p = [
                f"  — {x['name']}: позитивных>{x['positive_msgs']}, средн.тон "
                f"{x.get('avg_tone') if x.get('avg_tone') is not None else '—'} ({x['scored_msgs']} размеч.)"
                for x in by_pos
            ]
            parts.append(
                "• По размеченным сообщениям (tone_score): выше средний тон:\n"
                + "\n".join(lines_a)
                + "\n• Больше всего «позитивных» сообщений (score>0.3):\n"
                + "\n".join(lines_p)
            )

    if "edges" in blocks:
        parts.append(f"• Рёбер в таблице связей (edges): {blocks['edges']['edges']:,}.")

    if "political" in blocks:
        parts.append(f"• Политических сигналов (marketing_signal_events): {blocks['political']['political_events']:,}.")

    return "\n".join(parts)


def answer_chat_analytics_question(
    question: str,
    *,
    chat_id: int | None = None,
    default_period_days: int = 30,
    use_llm: bool = False,
) -> dict[str, Any]:
    """
    Без LLM: intents → SQL по разделам.

    С use_llm=True: полный компактный снимок метрик → один вызов LLM (без текста сообщений).

    Keys: answer, facts, intents, used_llm, llm_model (если был LLM), snapshot_for_llm (если был LLM),
          partial, period_days, chat_id, hint
    """
    q_raw = (question or "").strip()
    if not q_raw:
        return {
            "answer": "Введите вопрос (например: «сколько сообщений за неделю?», «кто самый активный?»).",
            "facts": {},
            "intents": [],
            "used_llm": False,
            "partial": False,
            "hint": _examples_hint(),
        }

    period_days = infer_period_days(q_raw, default=default_period_days)
    since = _since_param(period_days)
    chat_sql, chat_params = _scope_sql(chat_id)
    edge_sql, edge_params = _scope_edges(chat_id)

    if use_llm:
        blocks: dict[str, Any] = {}
        partial_snap = False
        with sync_session_scope() as session:
            dialect = session.bind.dialect.name
            is_pg = dialect == "postgresql"
            blocks, partial_snap = _collect_full_snapshot(
                session,
                is_pg,
                since,
                chat_sql,
                chat_params,
                edge_sql,
                edge_params,
                chat_id,
                period_days,
            )
        slim = _snapshot_for_llm_payload(blocks, period_days=period_days, chat_id=chat_id)
        answer, model_used = _answer_with_llm(q_raw, slim)
        return {
            "answer": answer,
            "facts": blocks,
            "snapshot_for_llm": slim,
            "intents": ["llm_compact_snapshot"],
            "used_llm": True,
            "llm_model": model_used,
            "partial": partial_snap,
            "period_days": period_days,
            "chat_id": chat_id,
            "hint": _examples_hint(),
        }

    intents = detect_intents(q_raw)
    if intents:
        intents = _strip_overview_duplicates(intents)
    elif _FALLBACK_OVERVIEW_RX.search(q_raw):
        intents = ["overview"]
    else:
        return {
            "answer": "Не удалось понять запрос по ключевым словам. Включите «Ответ через ИИ» для произвольных вопросов или переформулируйте. "
            + _examples_hint(),
            "facts": {},
            "intents": [],
            "used_llm": False,
            "partial": True,
            "hint": _examples_hint(),
        }

    blocks_rb: dict[str, Any] = {}
    partial = False

    with sync_session_scope() as session:
        dialect = session.bind.dialect.name
        is_pg = dialect == "postgresql"

        for intent in intents:
            try:
                if intent == "overview":
                    blocks_rb["overview"] = _run_overview(session, is_pg, since, chat_sql, chat_params)
                    blocks_rb["overview"]["period_days_used"] = period_days
                elif intent == "tone_distribution":
                    blocks_rb["tone_distribution"] = _run_tone_distribution(
                        session, is_pg, since, chat_sql, chat_params
                    )
                elif intent == "author_tone":
                    blocks_rb["author_tone_breakdown"] = _run_author_tone_breakdown(
                        session, is_pg, since, chat_sql, chat_params
                    )
                elif intent == "message_count":
                    blocks_rb["message_count"] = _run_message_count(session, since, chat_sql, chat_params)
                elif intent == "active_users":
                    blocks_rb["active_users"] = _run_active_users(session, since, chat_sql, chat_params)
                elif intent == "tone_avg":
                    blocks_rb["tone_avg"] = _run_tone_avg(session, since, chat_sql, chat_params)
                elif intent == "reply_rate":
                    blocks_rb["reply_rate"] = _run_reply_rate(session, since, chat_sql, chat_params)
                elif intent == "top_posters":
                    blocks_rb["top_posters"] = _run_top_posters(
                        session, is_pg, since, chat_sql, chat_params
                    )
                elif intent == "peak_time":
                    pk = _run_peak_time(session, is_pg, since, chat_sql, chat_params)
                    blocks_rb["peak_time"] = pk
                elif intent == "reply_pairs":
                    blocks_rb["reply_pairs"] = _run_reply_pairs(
                        session, is_pg, since, chat_sql, chat_params
                    )
                elif intent == "edges":
                    blocks_rb["edges"] = _run_edges_count(session, edge_sql, edge_params)
                elif intent == "political":
                    blocks_rb["political"] = _run_political_count(session, is_pg, since, chat_id)
            except Exception:
                logger.warning("analytics_chat_qa intent=%s failed", intent, exc_info=True)
                partial = True
                continue

    answer = _render_sections(intents, blocks_rb, period_days=period_days, chat_id=chat_id)
    if partial:
        answer += "\n\n(Часть метрик не удалось посчитать — см. лог сервера.)"

    return {
        "answer": answer,
        "facts": blocks_rb,
        "intents": intents,
        "used_llm": False,
        "partial": partial,
        "period_days": period_days,
        "chat_id": chat_id,
        "hint": _examples_hint(),
    }


def _examples_hint() -> str:
    return (
        "Примеры: «Краткая сводка за месяц», «Сколько сообщений за неделю», «Распределение тональности», "
        "«Кто больше всех пишет», «В какое время чат активнее», «Топ пар ответов», «Сколько политических сигналов», "
        "«Сколько рёбер в графе», «Кто самый позитивный / весёлый» (по размеченному тону сообщений). "
        "Укажите chat_id в форме, чтобы ограничить один чат. Произвольный вопрос — режим ИИ. "
        "Для статистики по людям нужны заполненные tone_score (fill_tone_scores.py). "
        "Порог минимума сообщений на автора: ANALYTICS_QA_AUTHOR_TONE_MIN_MSGS (по умолчанию 3)."
    )
