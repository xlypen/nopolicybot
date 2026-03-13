import time
import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from api.dependencies import require_auth
from api.routers.graph import router as graph_router
from api.routers.health import router as health_router
from api.routers.realtime import router as realtime_router
from api.routers.realtime import start_realtime_worker, stop_realtime_worker
from db.engine import init_db
from services.audit_log import read_recent, write_event
from services.monitoring import build_alerts, record_request, snapshot, to_prometheus_text
from services.rate_limiter import RateLimiter
from services.structured_logging import configure_logging

load_dotenv()
configure_logging("api-v2")

app = FastAPI(title="Political Monitor API v2", version="2.0.0", docs_url="/api/v2/docs", redoc_url="/api/v2/redoc")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
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


@app.on_event("startup")
async def startup():
    await init_db()
    await start_realtime_worker()


@app.on_event("shutdown")
async def shutdown():
    await stop_realtime_worker()


@app.get("/api/v2/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/api/v2/metrics")
async def metrics(format: str = Query(default="json"), _auth=Depends(require_auth)):
    snap = snapshot("api_v2")
    fmt = str(format or "json").strip().lower()
    if fmt in {"prom", "prometheus", "text"}:
        return PlainTextResponse(to_prometheus_text(snap, prefix="nopolicybot_api_v2"), media_type="text/plain; version=0.0.4")
    return {"ok": True, "metrics": snap}


@app.get("/api/v2/alerts")
async def alerts(limit: int = Query(default=120, ge=1, le=500), _auth=Depends(require_auth)):
    snap = snapshot("api_v2")
    audit_rows = read_recent(limit=limit)
    return {"ok": True, "alerts": build_alerts(snap, audit_rows), "metrics": snap, "audit_events": audit_rows[-20:]}


app.include_router(health_router, prefix="/api/v2", tags=["health"])
app.include_router(graph_router, prefix="/api/v2/graph", tags=["graph"])
app.include_router(realtime_router, prefix="/api/v2/realtime", tags=["realtime"])
