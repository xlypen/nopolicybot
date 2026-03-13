import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, APIStatusError

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Бесплатные модели OpenRouter (:free) — пробуем при 402
FALLBACK_FREE_MODELS = [
    "google/gemma-2-9b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
    "qwen/qwen-2-7b-instruct:free",
    "microsoft/phi-3-mini-128k-instruct:free",
]


def load_project_env() -> None:
    load_dotenv(ENV_PATH, encoding="utf-8-sig", override=True)


def _fix_ssl_certs_for_cyrillic_paths() -> None:
    """
    На Windows при пути пользователя с кириллицей (C:\\Users\\Сергей\\...)
    SSL не может загрузить сертификаты certifi — «No such file or directory».
    Копируем cacert.pem в проект (ASCII-путь) и задаём SSL_CERT_FILE.
    """
    if sys.platform != "win32":
        return
    try:
        import certifi
        cert_path = Path(certifi.where())
        if cert_path.as_posix().isascii():
            return
        # Путь содержит не-ASCII — копируем в проект
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


def _is_402(e: Exception) -> bool:
    if isinstance(e, APIStatusError):
        return getattr(e, "status_code", 0) == 402
    s = str(e).lower()
    return "402" in s or "insufficient" in s or "payment" in s or "credits" in s


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
