from __future__ import annotations

import os
import re
import sys
from urllib.parse import urlparse

_KNOWN_INSECURE_VALUES = {
    "",
    "none",
    "null",
    "nil",
    "changeme",
    "change-me",
    "change-me-in-production",
    "change-me-in-production-min-32-chars",
    "change-me-flask-secret",
    "your-telegram-bot-token",
    "your_telegram_bot_token",
    "your-bot-token",
    "your-openai-api-key",
    "your_admin_token",
    "default",
    "default-token",
}

_SERVICE_REQUIRED = {
    "bot": ("TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "DATABASE_URL"),
    "admin": ("ADMIN_SECRET_KEY", "DATABASE_URL"),
    "api": ("ADMIN_TOKEN", "DATABASE_URL"),
}


def _validation_disabled() -> bool:
    raw = str(os.getenv("SECRETS_VALIDATE", "1") or "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    return "pytest" in sys.modules


def _is_insecure_literal(value: str) -> bool:
    norm = str(value or "").strip().lower()
    if norm in _KNOWN_INSECURE_VALUES:
        return True
    if "change-me" in norm or "replace-me" in norm:
        return True
    if norm.startswith("your-") or norm.startswith("your_"):
        return True
    return False


def _require_non_empty(name: str, value: str, errors: list[str]) -> None:
    if not str(value or "").strip():
        errors.append(f"{name} is not set")
        return
    if _is_insecure_literal(value):
        errors.append(f"{name} uses insecure default/placeholder value")


def _validate_database_url(url: str, errors: list[str]) -> None:
    raw = str(url or "").strip()
    if not raw:
        errors.append("DATABASE_URL is not set")
        return
    if _is_insecure_literal(raw):
        errors.append("DATABASE_URL uses insecure default/placeholder value")
        return
    parsed = urlparse(raw)
    scheme = str(parsed.scheme or "").lower()
    if scheme.startswith("sqlite"):
        return
    password = parsed.password or ""
    if not password:
        errors.append("DATABASE_URL must include a DB password for non-sqlite backends")
        return
    if _is_insecure_literal(password):
        errors.append("DATABASE_URL contains insecure DB password")
        return
    if re.search(r"(postgres:postgres@|user:pass@|root:root@)", raw.lower()):
        errors.append("DATABASE_URL contains known default credentials")


def _validate_bot_token(value: str, errors: list[str]) -> None:
    token = str(value or "").strip()
    if not token:
        return
    if ":" not in token or len(token) < 40:
        errors.append("TELEGRAM_BOT_TOKEN has invalid format")


def _validate_min_length(name: str, value: str, min_len: int, errors: list[str]) -> None:
    val = str(value or "").strip()
    if val and len(val) < int(min_len):
        errors.append(f"{name} is too short (min {int(min_len)} chars)")


def validate_secrets(service: str, *, force: bool = False) -> None:
    service_norm = str(service or "").strip().lower()
    if service_norm not in _SERVICE_REQUIRED:
        raise ValueError(f"unknown service for secret validation: {service}")
    if not force and _validation_disabled():
        return

    try:
        from config.database_url import materialize_database_url_env

        materialize_database_url_env()
    except ImportError:
        pass

    errors: list[str] = []
    required = _SERVICE_REQUIRED[service_norm]
    values = {name: str(os.getenv(name, "") or "").strip() for name in required}
    for name, value in values.items():
        if name == "DATABASE_URL":
            continue
        _require_non_empty(name, value, errors)

    _validate_database_url(str(os.getenv("DATABASE_URL", "") or "").strip(), errors)

    if service_norm == "bot":
        _validate_bot_token(values.get("TELEGRAM_BOT_TOKEN", ""), errors)
        _validate_min_length("OPENAI_API_KEY", values.get("OPENAI_API_KEY", ""), 16, errors)
    elif service_norm == "admin":
        _validate_min_length("ADMIN_SECRET_KEY", values.get("ADMIN_SECRET_KEY", ""), 24, errors)
        admin_password = str(os.getenv("ADMIN_PASSWORD", "") or "").strip()
        if admin_password:
            if _is_insecure_literal(admin_password):
                errors.append("ADMIN_PASSWORD uses insecure default/placeholder value")
            _validate_min_length("ADMIN_PASSWORD", admin_password, 10, errors)
    elif service_norm == "api":
        _validate_min_length("ADMIN_TOKEN", values.get("ADMIN_TOKEN", ""), 24, errors)

    participant_secret = str(os.getenv("PARTICIPANT_SECRET", "") or "").strip()
    if participant_secret and _is_insecure_literal(participant_secret):
        errors.append("PARTICIPANT_SECRET uses insecure default/placeholder value")

    if errors:
        joined = "\n - ".join(errors)
        raise RuntimeError(
            f"Secret validation failed for service '{service_norm}'.\n"
            f"Fix the following before startup:\n - {joined}"
        )

