from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import admin_app
from api.main import app as fastapi_app


def main() -> int:
    with admin_app.app.test_client() as flask_client:
        r = flask_client.get("/health")
        if r.status_code != 200:
            raise RuntimeError(f"Flask health failed: {r.status_code}")
        body = r.get_json() or {}
        if body.get("status") != "ok":
            raise RuntimeError("Flask health payload invalid")

    with TestClient(fastapi_app) as api_client:
        r = api_client.get("/api/v2/health")
        if r.status_code != 200:
            raise RuntimeError(f"FastAPI health failed: {r.status_code}")
        body = r.json() or {}
        if body.get("status") != "ok":
            raise RuntimeError("FastAPI health payload invalid")

    print("smoke_checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
