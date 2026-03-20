import time
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

# До импорта db.engine (пул и DATABASE_URL из .env)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from fastapi import Depends, FastAPI, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from config.validate_secrets import validate_secrets
from api.dependencies import require_auth
from api.routers.admin import router as admin_router, start_admin_cache_warmer, stop_admin_cache_warmer
from api.routers.graph import router as graph_router
from api.routers.health import router as health_router
from api.routers.metrics import router as metrics_router
from api.routers.personality import router as personality_router
from api.routers.portrait import router as portrait_router
from api.routers.predictive import router as predictive_router
from api.routers.recommendations import router as recommendations_router
from api.routers.realtime import router as realtime_router
from api.routers.settings import router as settings_router
from api.routers.storage import router as storage_router
from api.routers.realtime import get_realtime_stats_snapshot, start_realtime_worker, stop_realtime_worker
from db.engine import init_db
from services.sqlite_storage import init_storage
from services.audit_log import read_recent, write_event
from services.monitoring import build_alerts, record_request, snapshot, to_prometheus_text
from services.rate_limiter import RateLimiter
from services.structured_logging import configure_logging

configure_logging("api-v2")


def _allowed_origins() -> list[str]:
    raw = str(os.getenv("ALLOWED_ORIGINS", "") or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for item in raw.split(","):
        origin = str(item or "").strip().rstrip("/")
        if origin and origin not in out:
            out.append(origin)
    return out


def _normalize_origin(origin: str) -> str:
    src = str(origin or "").strip().rstrip("/")
    if not src:
        return ""
    try:
        parsed = urlsplit(src)
    except Exception:
        return src.lower()
    scheme = str(parsed.scheme or "").strip().lower()
    host = str(parsed.hostname or "").strip().lower()
    port = parsed.port
    if not scheme or not host:
        return src.lower()
    if (scheme == "http" and (port is None or int(port) == 80)) or (scheme == "https" and (port is None or int(port) == 443)):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{int(port)}"


def _request_origin(request: Request) -> str:
    scheme = str(request.headers.get("x-forwarded-proto", "") or "").split(",")[0].strip().lower()
    host = str(request.headers.get("x-forwarded-host", "") or "").split(",")[0].strip()
    if not host:
        host = str(request.headers.get("host", "") or "").strip()
    if not scheme:
        scheme = str(request.url.scheme or "").strip().lower() or "http"
    if not host:
        return ""
    return _normalize_origin(f"{scheme}://{host}")


def _origin_allowed(origin: str, request: Request) -> bool:
    src = _normalize_origin(origin)
    if not src:
        return True
    req_origin = _request_origin(request)
    if req_origin and src == req_origin:
        return True
    allowed = {_normalize_origin(item) for item in _allowed_origins()}
    allowed.discard("")
    if not allowed:
        return False
    return src in allowed


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_secrets("api")
    await init_db()
    init_storage()
    await start_realtime_worker()
    start_admin_cache_warmer()
    try:
        yield
    finally:
        await stop_admin_cache_warmer()
        await stop_realtime_worker()


app = FastAPI(
    title="Political Monitor API v2",
    version="2.0.0",
    docs_url="/api/v2/docs",
    redoc_url="/api/v2/redoc",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=_allowed_origins(), allow_methods=["*"], allow_headers=["*"])
_API_RATE_LIMITER = RateLimiter(namespace="api_v2_ratelimit")


def _hardening_config() -> dict:
    return {
        "rate_limit_per_min": max(20, int(os.getenv("API_RATE_LIMIT_PER_MIN", "240"))),
        "max_url_length": max(256, int(os.getenv("API_MAX_URL_LENGTH", "2400"))),
        "max_body_bytes": max(1024, int(os.getenv("API_MAX_BODY_BYTES", "1048576"))),
    }


def _client_ip(request: Request) -> str:
    xff = str(request.headers.get("x-forwarded-for", "") or "").strip()
    if xff:
        return xff.split(",")[0].strip()[:64] or "unknown"
    if request.client and request.client.host:
        return str(request.client.host)[:64]
    return "unknown"


@app.middleware("http")
async def monitoring_middleware(request: Request, call_next):
    started = time.perf_counter()
    path = str(request.url.path or "/")
    cfg = _hardening_config()
    if path.startswith("/api/v2"):
        origin = str(request.headers.get("origin", "") or "").strip()
        if origin and not _origin_allowed(origin, request):
            write_event(
                "request_blocked_origin_not_allowed",
                severity="warning",
                source="api_v2",
                payload={"path": path, "method": request.method, "origin": origin[:240]},
            )
            return JSONResponse({"ok": False, "error": "origin not allowed"}, status_code=403)
    if path.startswith("/api/v2") and path not in {"/api/v2/health"}:
        full_url = str(request.url)
        if len(full_url) > int(cfg["max_url_length"]):
            write_event(
                "request_blocked_url_too_long",
                severity="warning",
                source="api_v2",
                payload={"path": path, "method": request.method, "url_length": len(full_url)},
            )
            return JSONResponse({"ok": False, "error": "url too long"}, status_code=414)
        try:
            content_length = int(request.headers.get("content-length", "0") or 0)
        except Exception:
            content_length = 0
        if content_length > int(cfg["max_body_bytes"]):
            write_event(
                "request_blocked_body_too_large",
                severity="warning",
                source="api_v2",
                payload={"path": path, "method": request.method, "content_length": int(content_length)},
            )
            return JSONResponse({"ok": False, "error": "payload too large"}, status_code=413)
        ip = _client_ip(request)
        rl = _API_RATE_LIMITER.hit(f"ip:{ip}", int(cfg["rate_limit_per_min"]), 60)
        if not bool(rl.get("allowed")):
            write_event(
                "request_rate_limited",
                severity="warning",
                source="api_v2",
                payload={"path": path, "method": request.method, "ip": ip, "limit": rl.get("limit")},
            )
            return JSONResponse(
                {"ok": False, "error": "rate limit exceeded", "retry_after": int(rl.get("retry_after", 1) or 1)},
                status_code=429,
                headers={"Retry-After": str(int(rl.get("retry_after", 1) or 1))},
            )
    try:
        response = await call_next(request)
    except Exception as e:
        elapsed = (time.perf_counter() - started) * 1000.0
        record_request("api_v2", request.method, path, 500, elapsed)
        write_event(
            "api_exception",
            severity="error",
            source="api_v2",
            payload={"path": path, "method": request.method, "error": str(e)},
        )
        raise
    elapsed = (time.perf_counter() - started) * 1000.0
    record_request("api_v2", request.method, path, int(response.status_code), elapsed)
    if int(response.status_code) >= 500:
        write_event(
            "api_5xx_response",
            severity="error",
            source="api_v2",
            payload={"path": path, "method": request.method, "status_code": int(response.status_code)},
        )
    return response


@app.get("/api/v2/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/api/v2/metrics")
async def metrics(format: str = Query(default="json"), _auth=Depends(require_auth)):
    snap = snapshot("api_v2")
    realtime_stats = await get_realtime_stats_snapshot()
    fmt = str(format or "json").strip().lower()
    if fmt in {"prom", "prometheus", "text"}:
        base = to_prometheus_text(snap, prefix="nopolicybot_api_v2")
        lines = [base.rstrip("\n")]
        util = (realtime_stats or {}).get("ws_queue_utilization") or {}
        lines.append("# HELP nopolicybot_api_v2_ws_queue_utilization WebSocket queue utilization by chat")
        lines.append("# TYPE nopolicybot_api_v2_ws_queue_utilization gauge")
        for chat_id, row in util.items():
            try:
                val = float((row or {}).get("max", 0.0) or 0.0)
            except Exception:
                val = 0.0
            lines.append(f'nopolicybot_api_v2_ws_queue_utilization{{chat_id="{chat_id}"}} {val}')
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
    return {"ok": True, "metrics": snap, "realtime": realtime_stats}


@app.get("/api/v2/alerts")
async def alerts(limit: int = Query(default=120, ge=1, le=500), _auth=Depends(require_auth)):
    snap = snapshot("api_v2")
    audit_rows = read_recent(limit=limit)
    return {"ok": True, "alerts": build_alerts(snap, audit_rows), "metrics": snap, "audit_events": audit_rows[-20:]}


@app.delete("/api/v2/users/{user_id}/data")
async def erase_user_data(user_id: int = Path(..., ge=1), _auth=Depends(require_auth)):
    from services.data_privacy import erase_user_data as erase_user_data_impl

    result = await erase_user_data_impl(int(user_id))
    write_event(
        "user_data_erased",
        severity="warning",
        source="api_v2",
        payload={"user_id": int(user_id), "result": result},
    )
    return {"ok": True, "result": result}


app.include_router(health_router, prefix="/api/v2", tags=["health"])
app.include_router(graph_router, prefix="/api/v2/graph", tags=["graph"])
app.include_router(admin_router, prefix="/api/v2/admin", tags=["admin"])
app.include_router(metrics_router, prefix="/api/v2/metrics", tags=["metrics"])
app.include_router(personality_router, prefix="/api/v2/personality", tags=["personality"])
app.include_router(portrait_router, prefix="/api/v2/portrait", tags=["portrait"])
app.include_router(recommendations_router, prefix="/api/v2/recommendations", tags=["recommendations"])
app.include_router(predictive_router, prefix="/api/v2/predictive", tags=["predictive"])
app.include_router(settings_router, prefix="/api/v2", tags=["settings"])
app.include_router(storage_router, prefix="/api/v2/storage", tags=["storage"])
app.include_router(realtime_router, prefix="/api/v2/realtime", tags=["realtime"])
