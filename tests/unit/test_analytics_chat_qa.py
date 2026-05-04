"""Юнит-тесты маршрутизации analytics_chat_qa (без БД)."""

from services.analytics_chat_qa import (
    _strip_overview_duplicates,
    detect_intents,
    infer_period_days,
)


def test_infer_period_days_week_month_year():
    assert infer_period_days("статистика за неделю", default=30) == 7
    assert infer_period_days("за месяц", default=14) == 30
    assert infer_period_days("за год", default=30) == 365
    assert infer_period_days("за 14 дней", default=30) == 14


def test_infer_period_default():
    assert infer_period_days("просто текст без периода", default=30) == 30


def test_detect_intents_peak_and_political():
    q = "В какое время суток чат активнее всего?"
    assert "peak_time" in detect_intents(q)
    assert "political" in detect_intents("Сколько было политических сигналов?")


def test_strip_overview_drops_redundant():
    raw = ["overview", "message_count", "tone_avg", "tone_distribution"]
    out = _strip_overview_duplicates(raw)
    assert "overview" in out
    assert "tone_distribution" in out
    assert "message_count" not in out
    assert "tone_avg" not in out
