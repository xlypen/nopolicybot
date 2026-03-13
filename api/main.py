from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers.graph import router as graph_router
from api.routers.health import router as health_router
from api.routers.realtime import router as realtime_router
from api.routers.realtime import start_realtime_worker, stop_realtime_worker
from db.engine import init_db

load_dotenv()

app = FastAPI(title="Political Monitor API v2", version="2.0.0", docs_url="/api/v2/docs", redoc_url="/api/v2/redoc")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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


app.include_router(health_router, prefix="/api/v2", tags=["health"])
app.include_router(graph_router, prefix="/api/v2/graph", tags=["graph"])
app.include_router(realtime_router, prefix="/api/v2/realtime", tags=["realtime"])
