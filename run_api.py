"""
Production entrypoint для FastAPI v2.
Запуск: python run_api.py

Память: каждый worker uvicorn — отдельный процесс с полной копией приложения.
На VPS 1–2 GB: API_WORKERS=1 и/или LOW_MEMORY_SERVER=1 в .env
"""

import os

import uvicorn


def _resolve_workers() -> int:
    """Число процессов uvicorn. Для слабого сервера — 1."""
    if os.getenv("LOW_MEMORY_SERVER", "").strip().lower() in ("1", "true", "yes", "on"):
        return 1
    try:
        w = int(os.getenv("API_WORKERS", "1"))
    except ValueError:
        w = 1
    try:
        cap = int(os.getenv("API_WORKERS_MAX", "4"))
    except ValueError:
        cap = 4
    return max(1, min(max(1, cap), w))


if __name__ == "__main__":
    port = int(os.getenv("API_PORT", 8001))
    workers = _resolve_workers()
    log_level = os.getenv("API_LOG_LEVEL", "info")
    reload = os.getenv("API_RELOAD", "false").lower() == "true"
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=port,
        workers=workers if not reload else 1,
        log_level=log_level,
        reload=reload,
        access_log=True,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
