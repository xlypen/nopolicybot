"""Centralized data hub — единый источник лейблов и данных.

Все словари (TOPIC_RU, ROLE_RU и т.д.) берутся из utils.labels.
Функции get_connections и get_top_topics используют реальные данные из
social_graph и user_stats соответственно.
"""
from utils.labels import (
    TONE_RU,
    TOPIC_RU,
    TOPIC_EMOJI,
    ALL_TOPICS,
    ROLE_RU,
    ROLE_EMOJI,
    ROLE_DESC,
    RANK_DESC,
    ALERT_RU,
    ALERT_PRIORITY,
    TONE_TREND_RU,
    PAIR_CONTEXT_RU,
)


def get_connections(chat_id=None):
    """Возвращает список связей из social_graph (реальные данные)."""
    from social_graph import get_connections as _gc
    return _gc(chat_id)


def get_top_topics(user_id: int, n: int = 3):
    """Возвращает топ-N тем пользователя из сохранённых данных (после refresh_derived_fields)."""
    from user_stats import get_user
    u = get_user(user_id)
    topics = u.get("topics") or {}
    if isinstance(topics, dict):
        return [t for t, _ in sorted(topics.items(), key=lambda x: -x[1])][:n]
    if isinstance(topics, list):
        return topics[:n]
    return []


def get_user_role(user_id: int):
    """Возвращает top_role пользователя (после refresh_derived_fields)."""
    from user_stats import get_user
    u = get_user(user_id)
    return u.get("top_role") or "participant"


__all__ = [
    "TONE_RU",
    "TOPIC_RU",
    "TOPIC_EMOJI",
    "ALL_TOPICS",
    "ROLE_RU",
    "ROLE_EMOJI",
    "ROLE_DESC",
    "RANK_DESC",
    "ALERT_RU",
    "ALERT_PRIORITY",
    "TONE_TREND_RU",
    "PAIR_CONTEXT_RU",
    "get_connections",
    "get_top_topics",
    "get_user_role",
]
