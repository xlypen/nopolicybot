"""Публичный базовый URL для ссылок «Мой профиль» (/me) из бота и шаблонов."""

import os

_ENV_KEYS = (
    "PARTICIPANT_BASE_URL",
    "ADMIN_BASE_URL",
    "PUBLIC_BASE_URL",
)


def get_participant_base_url() -> str:
    """Возвращает origin без завершающего «/» или пустую строку."""
    for key in _ENV_KEYS:
        v = (os.getenv(key) or "").strip().rstrip("/")
        if v:
            return v
    return ""
