import json
import logging
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, APIStatusError

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_logger = logging.getLogger(__name__)

# Бесплатные модели OpenRouter (:free) — пробуем при 402
FALLBACK_FREE_MODELS = [
    "google/gemma-2-9b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
    "qwen/qwen-2-7b-instruct:free",
    "microsoft/phi-3-mini-128k-instruct:free",
]

# Флаг: кредиты OpenRouter закончились — переключаемся на бесплатный путь
_openrouter_credits_exhausted = False


def load_project_env() -> None:
    load_dotenv(ENV_PATH, encoding="utf-8-sig", override=True)


def _fix_ssl_certs_for_cyrillic_paths() -> None:
    if sys.platform != "win32":
        return
    try:
        import certifi
        cert_path = Path(certifi.where())
        if cert_path.as_posix().isascii():
            return
        dest_dir = PROJECT_ROOT / ".certs"
        dest_dir.mkdir(exist_ok=True)
        dest_file = dest_dir / "cacert.pem"
        if not dest_file.exists() or dest_file.stat().st_mtime < cert_path.stat().st_mtime:
            shutil.copy2(cert_path, dest_file)
        os.environ["SSL_CERT_FILE"] = str(dest_file)
        os.environ["REQUESTS_CA_BUNDLE"] = str(dest_file)
    except Exception:
        pass


def get_client() -> OpenAI:
    load_project_env()
    _fix_ssl_certs_for_cyrillic_paths()
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    if not api_key:
        raise ValueError("Не задан OPENAI_API_KEY в .env")
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def is_credits_exhausted() -> bool:
    """Проверить, закончились ли платные кредиты OpenRouter."""
    return _openrouter_credits_exhausted


def mark_credits_exhausted() -> None:
    """Пометить что кредиты OpenRouter кончились."""
    global _openrouter_credits_exhausted
    if not _openrouter_credits_exhausted:
        _openrouter_credits_exhausted = True
        _logger.warning("OpenRouter credits exhausted — switching to free models")


def reset_credits_exhausted() -> None:
    """Сбросить флаг (после пополнения баланса OpenRouter)."""
    global _openrouter_credits_exhausted
    if _openrouter_credits_exhausted:
        _openrouter_credits_exhausted = False
        _logger.info("OpenRouter credits flag reset (paid request succeeded)")


def _is_402(e: Exception) -> bool:
    if isinstance(e, APIStatusError):
        if getattr(e, "status_code", 0) == 402:
            mark_credits_exhausted()
            return True
    s = str(e).lower()
    if "402" in s or "insufficient" in s or "payment" in s or "credits" in s:
        mark_credits_exhausted()
        return True
    return False


def gemini_chat_complete(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
) -> str | None:
    """
    Прямой вызов Gemini REST API (бесплатно, 500 req/day).
    Fallback когда OpenRouter недоступен.
    """
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        return None
    model = (model or os.getenv("GEMINI_MODEL") or "gemini-2.0-flash").strip()
    try:
        system_text = ""
        contents = []
        for m in messages:
            role = (m.get("role") or "user").lower()
            content = (m.get("content") or "").strip()
            if not content:
                continue
            if role == "system":
                system_text = content
            else:
                gemini_role = "user" if role == "user" else "model"
                contents.append({"role": gemini_role, "parts": [{"text": content}]})
        if not contents:
            return None
        payload = {
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
            },
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        payload["contents"] = contents
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        candidates = data.get("candidates") or []
        if not candidates:
            block = data.get("promptFeedback", {})
            if block.get("blockReason"):
                _logger.warning("Gemini blocked: %s", block)
            return None
        result = ""
        for part in (candidates[0].get("content") or {}).get("parts") or []:
            result += part.get("text", "")
        return result.strip() if result.strip() else None
    except urllib.error.HTTPError as e:
        _logger.warning("Gemini API HTTP %s: %s", e.code, e.read().decode()[:200] if e.fp else "")
        return None
    except Exception as e:
        _logger.warning("Gemini direct API fallback failed: %s", e)
        return None


def gemini_analyze_image(image_bytes: bytes, prompt_text: str, *, mime: str = "image/jpeg") -> str | None:
    """
    Анализ изображения через Gemini Vision (бесплатно).
    Возвращает сырой текст ответа или None.
    """
    import base64
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        return None
    model = (os.getenv("GEMINI_MODEL") or "gemini-2.0-flash").strip()
    try:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        parts = [
            {"inlineData": {"mimeType": mime, "data": b64}},
            {"text": prompt_text},
        ]
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"maxOutputTokens": 500, "temperature": 0.2},
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        candidates = data.get("candidates") or []
        if not candidates:
            return None
        result = ""
        for part in (candidates[0].get("content") or {}).get("parts") or []:
            result += part.get("text", "")
        return result.strip() if result.strip() else None
    except Exception as e:
        _logger.warning("Gemini vision failed: %s", e)
        return None


def chat_complete_with_fallback(
    messages: list[dict],
    *,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.7,
    prefer_free: bool = False,
) -> tuple[str, str]:
    """
    Универсальная функция: пробует OpenRouter, при 402 — бесплатные модели,
    при полном отказе — прямой Gemini API.

    Returns: (text, model_used)
    """
    load_project_env()
    model = model or (os.getenv("OPENAI_MODEL") or "deepseek-chat").strip()

    if prefer_free or _openrouter_credits_exhausted:
        result = gemini_chat_complete(messages, max_tokens=max_tokens, temperature=temperature)
        if result:
            return result, "gemini-direct-free"

    try:
        client = get_client()
    except ValueError:
        result = gemini_chat_complete(messages, max_tokens=max_tokens, temperature=temperature)
        if result:
            return result, "gemini-direct-free"
        return "", "none"

    models_to_try = []
    if not _openrouter_credits_exhausted:
        models_to_try.append(model)
    for m in FALLBACK_FREE_MODELS:
        if m not in models_to_try:
            models_to_try.append(m)

    for m in models_to_try:
        try:
            resp = client.chat.completions.create(
                model=m,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                if m not in FALLBACK_FREE_MODELS:
                    reset_credits_exhausted()
                return text, m
        except Exception as e:
            if _is_402(e):
                continue
            _logger.warning("Model %s failed: %s", m, e)
            continue

    result = gemini_chat_complete(messages, max_tokens=max_tokens, temperature=temperature)
    if result:
        return result, "gemini-direct-free"

    return "", "none"


def prefer_free_mode() -> bool:
    """
    Использовать Gemini напрямую (бесплатно) вместо OpenRouter.
    True если: AI_PREFER_FREE=1, AI_USE_GEMINI_FIRST=1, или (есть GEMINI и нет OPENAI).
    При AI_USE_OPENROUTER_FIRST=1 и наличии OPENAI_API_KEY — приоритет у OpenRouter.
    """
    has_openai = bool((os.getenv("OPENAI_API_KEY") or "").strip())
    has_gemini = bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())
    if (os.getenv("AI_USE_OPENROUTER_FIRST") or "").strip().lower() in ("1", "true", "yes") and has_openai:
        return False
    if (os.getenv("AI_PREFER_FREE") or "").strip().lower() in ("1", "true", "yes"):
        return True
    if (os.getenv("AI_USE_GEMINI_FIRST") or "").strip().lower() in ("1", "true", "yes"):
        return True
    return has_gemini and not has_openai


def get_chat_models_for_fallback() -> list[str]:
    """Модели для fallback при 402: основная + бесплатные."""
    load_project_env()
    main = (os.getenv("OPENAI_MODEL") or "").strip()
    fc = (os.getenv("OPENAI_FACTCHECK_MODEL") or "").strip()
    models = []
    if fc:
        models.append(fc)
    if main and main not in models:
        models.append(main)
    for m in FALLBACK_FREE_MODELS:
        if m not in models:
            models.append(m)
    return models
