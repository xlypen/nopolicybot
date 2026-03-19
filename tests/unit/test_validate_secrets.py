from __future__ import annotations

import pytest

from config.validate_secrets import validate_secrets


def test_validate_secrets_rejects_insecure_bot_defaults(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "your-telegram-bot-token")
    monkeypatch.setenv("OPENAI_API_KEY", "change-me-in-production")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db")
    with pytest.raises(RuntimeError):
        validate_secrets("bot", force=True)


def test_validate_secrets_rejects_insecure_api_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "change-me-in-production")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db")
    with pytest.raises(RuntimeError):
        validate_secrets("api", force=True)


def test_validate_secrets_accepts_valid_values(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:abcdefghijklmnopqrstuvwxyzABCDEFGHijklmnop")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-valid-key-1234567890")
    monkeypatch.setenv("ADMIN_TOKEN", "token-secure-1234567890-abcdefgh")
    monkeypatch.setenv("ADMIN_SECRET_KEY", "super-secure-admin-secret-key-123")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app_user:VeryStrongPassword_123@localhost:5432/nopolicybot")
    validate_secrets("bot", force=True)
    validate_secrets("api", force=True)
    validate_secrets("admin", force=True)

