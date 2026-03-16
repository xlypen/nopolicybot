"""
Общий анализ чата: состав, активность, политический контур, модерация, риски.
Объединяет данные из user_stats, social_graph и bot_settings.
"""

import logging
from html import escape as esc

logger = logging.getLogger(__name__)


def build_chat_analysis(
    chat_id: int,
    period_days: int = 7,
    include_ai_summary: bool = False,
) -> dict:
    """
    Строит полный анализ чата. Возвращает dict с метриками и HTML-блоками.
    """
    cid = int(chat_id)
    days = max(1, min(int(period_days or 7), 30))

    from user_stats import get_users_in_chat, get_user_display_names, get_user
    from social_graph import get_connections, get_connections_for_digest, get_user_roles, get_conflict_forecast
    from utils.labels import TONE_RU, TOPIC_RU, ROLE_RU

    RANK_LABELS = {
        "loyal": "🇷🇺 Лояльный",
        "neutral": "⚪ Нейтральный",
        "opposition": "🔴 Оппозиция",
        "unknown": "❓ Неизвестно",
    }

    try:
        from bot_settings import get_chat_mode
        chat_mode = get_chat_mode(cid)
    except Exception:
        chat_mode = "default"

    user_ids = get_users_in_chat(cid)
    names = get_user_display_names()

    def _name(uid) -> str:
        return names.get(str(int(uid or 0)), str(uid))

    # --- user_stats: участники, ранги, полит, замечания ---
    users_data: list[dict] = []
    total_messages = 0
    total_political = 0
    total_warnings = 0
    pos_sent = neg_sent = neu_sent = 0
    ranks_count: dict[str, int] = {}
    users_with_warnings = 0

    for uid_str in user_ids:
        try:
            u = get_user(int(uid_str))
        except Exception:
            continue
        if not u:
            continue
        stats = u.get("stats") or {}
        tm = int(stats.get("total_messages", 0) or 0)
        pm = int(stats.get("political_messages", 0) or 0)
        wr = int(stats.get("warnings_received", 0) or 0)
        total_messages += tm
        total_political += pm
        total_warnings += wr
        pos_sent += int(stats.get("positive_sentiment", 0) or 0)
        neg_sent += int(stats.get("negative_sentiment", 0) or 0)
        neu_sent += int(stats.get("neutral_sentiment", 0) or 0)
        r = u.get("rank", "unknown")
        ranks_count[r] = ranks_count.get(r, 0) + 1
        if wr > 0:
            users_with_warnings += 1
        users_data.append({
            "user_id": uid_str,
            "display_name": u.get("display_name", uid_str),
            "rank": r,
            "total_messages": tm,
            "political_messages": pm,
            "warnings_received": wr,
            "positive": int(stats.get("positive_sentiment", 0) or 0),
            "negative": int(stats.get("negative_sentiment", 0) or 0),
            "neutral": int(stats.get("neutral_sentiment", 0) or 0),
        })

    users_data.sort(key=lambda x: -x["total_messages"])
    top_users = users_data[:5]

    # --- social_graph: связи, тон, темы (из БД при включённом режиме) ---
    metric_key = "message_count_7d" if days <= 7 else "message_count_30d"
    rows = [r for r in get_connections_for_digest(cid) if int(r.get(metric_key, 0) or r.get("message_count_30d", 0) or 0) > 0]
    if not rows:
        rows = list(get_connections_for_digest(cid))

    total_connections = len(rows)
    conn_messages = sum(int(r.get(metric_key, 0) or r.get("message_count_30d", 0) or 0) for r in rows)
    tone_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    for r in rows:
        tone = str(r.get("tone", "neutral") or "neutral")
        cnt = int(r.get(metric_key, 0) or r.get("message_count_30d", 0) or 0)
        tone_counts[tone] = tone_counts.get(tone, 0) + cnt
        for t in (r.get("topics") or []):
            topic_counts[t] = topic_counts.get(t, 0) + 1

    dominant_tone = max(tone_counts, key=tone_counts.get) if tone_counts else "neutral"
    top_topics = sorted(topic_counts.items(), key=lambda kv: -kv[1])[:5]
    roles = get_user_roles(cid, limit=20)
    conflicts = get_conflict_forecast(cid, limit=8)

    # --- Плотность ---
    participants = len(users_data)
    msgs_per_user_per_day = (total_messages / (participants * days)) if participants and days else 0

    # --- Портрет чата (структурированный) ---
    political_pct = round(100 * total_political / total_messages, 1) if total_messages else 0
    portrait_sections = _build_portrait_sections(
        participants=participants,
        total_messages=total_messages,
        total_political=total_political,
        political_pct=political_pct,
        total_warnings=total_warnings,
        ranks_count=ranks_count,
        dominant_tone=dominant_tone,
        tone_counts=tone_counts,
        top_topics=top_topics,
        conflicts=conflicts,
        top_users=top_users,
        roles=roles,
        pos_sent=pos_sent,
        neg_sent=neg_sent,
        neu_sent=neu_sent,
        tone_ru=TONE_RU,
        topic_ru=TOPIC_RU,
        names=names,
    )
    if include_ai_summary:
        ai_enriched = _generate_ai_portrait(portrait_sections, participants, total_messages)
        if ai_enriched:
            portrait_sections = ai_enriched

    return {
        "chat_id": cid,
        "period_days": days,
        "participants": participants,
        "total_messages": total_messages,
        "total_political": total_political,
        "political_pct": round(100 * total_political / total_messages, 1) if total_messages else 0,
        "total_warnings": total_warnings,
        "users_with_warnings": users_with_warnings,
        "pos_sentiment": pos_sent,
        "neg_sentiment": neg_sent,
        "neu_sentiment": neu_sent,
        "ranks_count": ranks_count,
        "top_users": top_users,
        "total_connections": total_connections,
        "conn_messages": conn_messages,
        "dominant_tone": dominant_tone,
        "tone_counts": tone_counts,
        "top_topics": top_topics,
        "roles": roles,
        "conflicts": conflicts,
        "chat_mode": chat_mode,
        "msgs_per_user_per_day": round(msgs_per_user_per_day, 1),
        "portrait_sections": portrait_sections,
        "names": names,
        "TONE_RU": TONE_RU,
        "TOPIC_RU": TOPIC_RU,
        "ROLE_RU": ROLE_RU,
        "RANK_LABELS": RANK_LABELS or {},
    }


def _build_portrait_sections(
    participants: int,
    total_messages: int,
    total_political: int,
    political_pct: float,
    total_warnings: int,
    ranks_count: dict,
    dominant_tone: str,
    tone_counts: dict,
    top_topics: list,
    conflicts: list,
    top_users: list,
    roles: list,
    pos_sent: int,
    neg_sent: int,
    neu_sent: int,
    tone_ru: dict,
    topic_ru: dict,
    names: dict | None = None,
) -> dict:
    """Строит структурированные секции портрета чата (как портрет человека)."""
    tone_label = tone_ru.get(dominant_tone, dominant_tone)
    r_labels = {"loyal": "лояльные", "neutral": "нейтральные", "opposition": "оппозиция", "unknown": "неизвестно"}

    # Психологический профиль
    psych_parts = [f"Тон общения: {tone_label}."]
    if tone_counts:
        mix = ", ".join(f"{tone_ru.get(t, t)}: {c}" for t, c in sorted(tone_counts.items(), key=lambda x: -x[1])[:3])
        psych_parts.append(f"Соотношение: {mix}.")
    if conflicts:
        psych_parts.append(f"Напряжённых пар: {len(conflicts)}.")
    if total_warnings:
        psych_parts.append(f"Замечаний модерации: {total_warnings}.")
    psychological = " ".join(psych_parts)

    # Профессиональный контур
    prof_parts = []
    work_tech = [(t, c) for t, c in top_topics if t in ("work", "technical")]
    if work_tech:
        t_str = ", ".join(f"{topic_ru.get(t, t)} ({c})" for t, c in work_tech)
        prof_parts.append(f"Рабочие и технические темы: {t_str}.")
    experts = [r for r in roles if r.get("role") == "expert"]
    if experts and names:
        expert_names = [names.get(str(r["user_id"]), str(r["user_id"])) for r in experts[:3]]
        prof_parts.append(f"Эксперты по темам: {', '.join(expert_names)}.")
    if top_users:
        prof_parts.append(f"Самые активные: {', '.join(u['display_name'] for u in top_users[:3])}.")
    professional = " ".join(prof_parts) if prof_parts else "Профессиональная тематика не выделена."

    # Политический профиль
    pol_parts = [f"Полит. сообщений: {total_political} ({political_pct}%)."]
    if ranks_count:
        r_str = ", ".join(f"{r_labels.get(k, k)}: {v}" for k, v in ranks_count.items())
        pol_parts.append(f"Ранги: {r_str}.")
    pol_parts.append(f"Настроения: +{pos_sent} / −{neg_sent} / 0{neu_sent}.")
    political = " ".join(pol_parts)

    # Частые темы
    if top_topics:
        topics = ", ".join(f"{topic_ru.get(t, t)} ({c})" for t, c in top_topics[:5])
    else:
        topics = "Данных о темах пока нет."
    topics_text = topics

    # Краткая сводка
    summary = f"Чат из {participants} участников, {total_messages} сообщений. {tone_label.capitalize()} атмосфера."

    return {
        "psychological": psychological,
        "professional": professional,
        "political": political,
        "topics": topics_text,
        "summary": summary,
    }


def _generate_ai_portrait(sections: dict, participants: int, total_messages: int) -> dict | None:
    """Обогащает портрет через AI. Возвращает dict с теми же ключами или None."""
    try:
        import json
        import os
        from ai_analyzer import get_client
        client = get_client()
        ctx = f"Участников: {participants}, сообщений: {total_messages}. Психологический: {sections['psychological']}. Профессиональный: {sections['professional']}. Политический: {sections['political']}. Темы: {sections['topics']}."
        prompt = f"""По данным чата напиши портрет чата как портрет человека — структурированно. Верни ТОЛЬКО валидный JSON без markdown:
{{
  "psychological": "2-3 предложения: атмосфера, тон, конфликты, психологический климат",
  "professional": "2-3 предложения: рабочие темы, экспертиза, профессиональный контекст",
  "political": "2-3 предложения: политический профиль, ранги, настроения",
  "topics": "частые темы для разговоров, перечисление",
  "summary": "1-2 предложения: общая характеристика чата"
}}

Данные: {ctx}"""
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "deepseek-chat"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        out = {}
        for k in ("psychological", "professional", "political", "topics", "summary"):
            out[k] = str(data.get(k, sections[k]))[:400] if data.get(k) else sections[k]
        return out
    except Exception as e:
        logger.warning("AI-портрет чата: %s", e)
        return None


def _render_portrait_sections(sections: dict, compact: bool = False, for_admin: bool = False) -> str:
    """Рендерит структурированный портрет чата (эргономичная вёрстка). for_admin — стиль админки (CSS vars)."""
    from html import escape as esc
    if not sections:
        if for_admin:
            return '<section class="analysis-section chat-portrait-section"><h3 class="digest-subtitle">Портрет чата</h3><p class="digest-muted">Данных нет.</p></section>'
        return '<section class="analysis-section"><h3>Портрет чата</h3><p style="color:#9bb0cf;">Данных нет.</p></section>'

    blocks = [
        ("psychological", "Психологический профиль", "🧠"),
        ("professional", "Профессиональный контур", "💼"),
        ("political", "Политический профиль", "📊"),
        ("topics", "Частые темы", "💬"),
        ("summary", "Краткая сводка", "📋"),
    ]

    h = []
    h.append('<section class="analysis-section chat-portrait-section" style="margin-bottom:1.5rem;">')
    h.append('<h3 class="digest-subtitle" style="margin:0 0 1rem;font-size:1.1rem;">Портрет чата</h3>')
    grid_style = "display:grid;grid-template-columns:1fr;gap:0.5rem;" if compact else "display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:0.75rem;"
    h.append(f'<div class="portrait-grid" style="{grid_style}">')

    for key, label, icon in blocks:
        text = sections.get(key, "")
        if not text:
            continue
        if for_admin:
            h.append(
                f'<div class="digest-dialogue portrait-card portrait-card--admin">'
                f'<div class="digest-dialogue-head portrait-card-title">{icon} {esc(label)}</div>'
                f'<div class="digest-dialogue-summary portrait-card-text">{esc(text)}</div>'
                f'</div>'
            )
        else:
            h.append(
                f'<div class="portrait-card" style="background:var(--bg-input);border:1px solid var(--border-card);'
                f'border-radius:8px;padding:0.75rem;border-left:4px solid #2d3a5c;">'
                f'<div class="portrait-card-title" style="font-size:0.8rem;font-weight:600;color:#9fb9da;margin-bottom:0.35rem;">{icon} {esc(label)}</div>'
                f'<div class="portrait-card-text" style="font-size:0.88rem;line-height:1.5;color:#d4e5ff;">{esc(text)}</div>'
                f'</div>'
            )
    h.append('</div></section>')
    return "\n".join(h)


def render_analysis_brief(data: dict, for_admin: bool = False) -> str:
    """Краткий блок для главной (index): метрики + портрет чата. for_admin — стиль админки."""
    if not data:
        if for_admin:
            return '<div class="digest-empty">Данных недостаточно для анализа.</div>'
        return '<div style="color:#9bb0cf;">Данных недостаточно для анализа.</div>'
    from html import escape as esc
    p = data["participants"]
    tm = data["total_messages"]
    pol = data["total_political"]
    pol_pct = data["political_pct"]
    warn = data["total_warnings"]
    tone = data["TONE_RU"].get(data["dominant_tone"], data["dominant_tone"])
    conflicts = data["conflicts"]
    h = []
    if for_admin:
        h.append('<div class="digest-title">Сводка за период</div>')
        h.append('<div class="digest-metrics">')
        for label, val in [
            ("Участников", str(p)),
            ("Сообщений", str(tm)),
            ("Полит. (%)", f"{pol} ({pol_pct}%)"),
            ("Замечаний", str(warn)),
            ("Тон", esc(tone)),
        ]:
            h.append(
                f'<div class="digest-metric"><span class="digest-metric-label">{esc(label)}</span>'
                f'<span class="digest-metric-value">{esc(str(val))}</span></div>'
            )
        h.append('</div>')
        if conflicts:
            h.append(f'<div class="digest-row" style="color:var(--color-warn);">⚠ Рисков конфликтов: {len(conflicts)}</div>')
        sections = data.get("portrait_sections") or {}
        h.append(_render_portrait_sections(sections, compact=True, for_admin=True))
    else:
        h.append('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:0.4rem;margin-bottom:0.5rem;">')
        for label, val in [
            ("Участников", str(p)),
            ("Сообщений", str(tm)),
            ("Полит. (%)", f"{pol} ({pol_pct}%)"),
            ("Замечаний", str(warn)),
            ("Тон", esc(tone)),
        ]:
            h.append(
                f'<div style="background:#11345c;border:1px solid #2b5f95;border-radius:7px;padding:0.35rem 0.5rem;">'
                f'<div style="font-size:0.7rem;color:#9fb9da;">{esc(label)}</div>'
                f'<div style="font-size:0.9rem;font-weight:700;color:#e8f2ff;">{esc(str(val))}</div></div>'
            )
        h.append('</div>')
        if conflicts:
            h.append(f'<div style="font-size:0.8rem;color:#e8a87c;margin-bottom:0.4rem;">⚠ Рисков конфликтов: {len(conflicts)}</div>')
        sections = data.get("portrait_sections") or {}
        h.append(_render_portrait_sections(sections, compact=True))
    return "\n".join(h)


def render_analysis_full(data: dict, chat_title: str = "") -> str:
    """Полный HTML-отчёт для страницы /chat/<id>/analysis."""
    if not data:
        return '<div style="color:#9bb0cf;">Данных недостаточно.</div>'
    from html import escape as esc
    h = []

    # Заголовок
    title = esc(chat_title or str(data["chat_id"]))
    h.append(f'<h2 style="margin-bottom:1rem;">Общий анализ: {title}</h2>')
    h.append(f'<p style="color:#9db4d1;font-size:0.9rem;margin-bottom:1rem;">Период: {data["period_days"]} дн.</p>')

    # 0. Портрет чата (структурированный, как портрет человека)
    sections = data.get("portrait_sections") or {}
    h.append(_render_portrait_sections(sections))

    # 1. Состав и активность
    h.append('<section class="analysis-section"><h3>Состав и активность</h3>')
    h.append('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:0.5rem;">')
    for label, val in [
        ("Участников", str(data["participants"])),
        ("Сообщений", str(data["total_messages"])),
        ("Сообщ./участ./день", str(data["msgs_per_user_per_day"])),
    ]:
        h.append(
            f'<div style="background:#11345c;border:1px solid #2b5f95;border-radius:8px;padding:0.5rem;">'
            f'<div style="font-size:0.75rem;color:#9fb9da;">{label}</div>'
            f'<div style="font-size:1.1rem;font-weight:700;">{esc(str(val))}</div></div>'
        )
    h.append('</div>')
    # Топ участников
    if data["top_users"]:
        parts = []
        for u in data["top_users"]:
            parts.append(f'<strong>{esc(u["display_name"])}</strong> ({u["total_messages"]})')
        h.append(f'<p style="margin-top:0.5rem;font-size:0.9rem;">Топ по активности: {", ".join(parts)}</p>')
    h.append('</section>')

    # 2. Политический контур
    h.append('<section class="analysis-section"><h3>Политический контур</h3>')
    h.append('<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:0.4rem;">')
    for label, val in [
        ("Полит. сообщений", str(data["total_political"])),
        ("% от общего", str(data["political_pct"]) + "%"),
        ("Тон: + / − / 0", f"{data['pos_sentiment']} / {data['neg_sentiment']} / {data['neu_sentiment']}"),
    ]:
        h.append(
            f'<div style="background:#1f4d3c;border:1px solid #2d7a5c;border-radius:8px;padding:0.4rem;">'
            f'<div style="font-size:0.72rem;color:#b8e6d0;">{label}</div>'
            f'<div style="font-size:0.95rem;font-weight:600;">{esc(str(val))}</div></div>'
        )
    h.append('</div>')
    # Ранги
    rl = data.get("RANK_LABELS") or {}
    rank_chips = " ".join(
        f'<span style="padding:0.2rem 0.5rem;border-radius:999px;background:#1a3a5c;color:#c9dcf5;font-size:0.8rem;">'
        f'{esc(rl.get(k, k))}: {v}</span>'
        for k, v in (data["ranks_count"] or {}).items()
    )
    if rank_chips:
        h.append(f'<p style="margin-top:0.5rem;">Ранги: {rank_chips}</p>')
    h.append('</section>')

    # 3. Модерация
    h.append('<section class="analysis-section"><h3>Модерация</h3>')
    h.append(
        f'<p>Замечаний: <strong>{data["total_warnings"]}</strong>, '
        f'участников с замечаниями: <strong>{data["users_with_warnings"]}</strong></p>'
    )
    mode_labels = {"default": "По умолчанию", "soft": "Мягкий", "active": "Активный", "beast": "Зверь", "custom": "Свои"}
    h.append(f'<p>Режим чата: <strong>{esc(mode_labels.get(data["chat_mode"], data["chat_mode"]))}</strong></p>')
    h.append('</section>')

    # 4. Социальный граф
    h.append('<section class="analysis-section"><h3>Социальный граф</h3>')
    h.append(
        f'<p>Активных связей: <strong>{data["total_connections"]}</strong>, '
        f'сообщений в диалогах: <strong>{data["conn_messages"]}</strong></p>'
    )
    tone_ru = data.get("TONE_RU") or {}
    h.append(f'<p>Доминирующий тон: <strong>{esc(tone_ru.get(data["dominant_tone"], data["dominant_tone"]))}</strong></p>')
    if data["top_topics"]:
        topic_ru = data.get("TOPIC_RU") or {}
        chips = " ".join(
            f'<span style="padding:0.15rem 0.4rem;border-radius:999px;background:#1f4d3c;color:#d2f7e4;font-size:0.78rem;">'
            f'{esc(topic_ru.get(k, k))} ({v})</span>'
            for k, v in data["top_topics"]
        )
        h.append(f'<p>Темы: {chips}</p>')
    if data["roles"]:
        role_ru = data.get("ROLE_RU") or {}
        names = data.get("names") or {}
        role_parts = [
            f'{esc(role_ru.get(r["role"], r["role"]))}: {esc(names.get(str(r["user_id"]), str(r["user_id"])))}'
            for r in data["roles"][:6]
        ]
        h.append(f'<p style="font-size:0.88rem;">Роли: {", ".join(role_parts)}</p>')
    h.append('</section>')

    # 5. Риски
    if data["conflicts"]:
        h.append('<section class="analysis-section"><h3>Риски конфликтов</h3>')
        names = data.get("names") or {}
        for c in data["conflicts"][:6]:
            uid_a, uid_b = c.get("user_a"), c.get("user_b")
            if uid_a == uid_b:
                continue
            ua = names.get(str(uid_a), str(uid_a))
            ub = names.get(str(uid_b), str(uid_b))
            risk = int((c.get("risk", 0) or 0) * 100)
            h.append(
                f'<div style="padding:0.4rem;margin-bottom:0.3rem;background:#3d1a1a;border:1px solid #6b2d2d;border-radius:6px;">'
                f'<strong>{esc(ua)}</strong> ↔ <strong>{esc(ub)}</strong> — риск {risk}%</div>'
            )
        h.append('</section>')

    return "\n".join(h)
