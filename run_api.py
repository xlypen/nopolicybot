"""
Production entrypoint для FastAPI v2.
Запуск: python run_api.py
"""

import os

import uvicorn


if __name__ == "__main__":
    port = int(os.getenv("API_PORT", 8001))
    workers = int(os.getenv("API_WORKERS", 2))
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
