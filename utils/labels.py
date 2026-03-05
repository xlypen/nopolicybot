"""Единый словарь переводов для UI: тон, темы, роли, алерты."""

TONE_RU = {
    "friendly": "дружелюбный",
    "neutral": "нейтральный",
    "conflict": "конфликтный",
    "toxic": "токсичный",
}

TOPIC_RU = {
    "general": "общее",
    "technical": "техническое",
    "work": "работа",
    "politics": "политика",
    "humor": "юмор",
    "personal": "личное",
}

ROLE_RU = {
    "connector": "связующий",
    "expert": "эксперт",
    "mediator": "медиатор",
    "provocateur": "провокатор",
    "participant": "участник",
}

ALERT_RU = {
    "new_connection": "новая связь",
    "rising_activity": "рост активности",
    "toxicity_spike": "риск токсичности",
}

ALERT_PRIORITY = {
    "toxicity_spike": 3,
    "rising_activity": 2,
    "new_connection": 1,
}
