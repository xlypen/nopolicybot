"""Эвристика: мат в вопросе по теме ≠ послать бота (пауза диалога)."""
from ai_analyzer import (
    _explicit_dismissal_or_insult_to_interlocutor,
    _looks_like_topic_swear_emphasis_not_pause,
)


def test_swear_in_topic_question_not_pause_signal():
    assert _looks_like_topic_swear_emphasis_not_pause("какие нахуй авиационные лейауты?")
    assert _looks_like_topic_swear_emphasis_not_pause("Иван: какие нахуй авиационные лейауты")
    assert _looks_like_topic_swear_emphasis_not_pause("что за хуйня с ценами бля?")


def test_explicit_dismissal_blocks_emphasis_override():
    assert _explicit_dismissal_or_insult_to_interlocutor("нахуй ты мне пишешь")
    assert not _looks_like_topic_swear_emphasis_not_pause("нахуй ты мне пишешь")
    assert _explicit_dismissal_or_insult_to_interlocutor("иди нахуй отсюда")
    assert not _looks_like_topic_swear_emphasis_not_pause("иди нахуй отсюда")


def test_no_profanity_no_signal():
    assert not _looks_like_topic_swear_emphasis_not_pause("какие авиационные лейауты?")
