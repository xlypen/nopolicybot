import time

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from api.dependencies import require_auth
from api.routers.graph import router as graph_router
from api.routers.health import router as health_router
from api.routers.realtime import router as realtime_router
from api.routers.realtime import start_realtime_worker, stop_realtime_worker
from db.engine import init_db
from services.audit_log import read_recent, write_event
from services.monitoring import build_alerts, record_request, snapshot, to_prometheus_text
from services.structured_logging import configure_logging

load_dotenv()
configure_logging("api-v2")

app = FastAPI(title="Political Monitor API v2", version="2.0.0", docs_url="/api/v2/docs", redoc_url="/api/v2/redoc")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def monitoring_middleware(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as e:
        elapsed = (time.perf_counter() - started) * 1000.0
        record_request("api_v2", request.method, request.url.path, 500, elapsed)
        write_event(
            "api_exception",
            severity="error",
            source="api_v2",
            payload={"path": request.url.path, "method": request.method, "error": str(e)},
        )
        raise
    elapsed = (time.perf_counter() - started) * 1000.0
    record_request("api_v2", request.method, request.url.path, int(response.status_code), elapsed)
    if int(response.status_code) >= 500:
        write_event(
            "api_5xx_response",
            severity="error",
            source="api_v2",
            payload={"path": request.url.path, "method": request.method, "status_code": int(response.status_code)},
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
