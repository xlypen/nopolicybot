"""Storage status and cutover API."""

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Depends, Query

from api.dependencies import require_auth
from services.data_platform import export_snapshot
from services.storage_cutover import apply_cutover, build_cutover_report

router = APIRouter()


@router.get("/status")
async def get_storage_status(_auth=Depends(require_auth)):
    payload = await asyncio.to_thread(export_snapshot)
    return payload


@router.get("/cutover-report")
async def get_cutover_report(_auth=Depends(require_auth)):
    payload = await asyncio.to_thread(build_cutover_report)
    return payload


@router.post("/cutover")
async def post_cutover(
    body: dict = Body(default_factory=dict),
    _auth=Depends(require_auth),
):
    mode = str(body.get("mode") or "").strip().lower()
    force = bool(body.get("force", False))
    reason = str(body.get("reason") or "manual").strip()
    allowed_modes = {"json", "hybrid", "db", "dual", "db_first", "db_only"}
    if mode not in allowed_modes:
        return {
            "ok": False,
            "error": "mode must be one of: json, hybrid, db, dual, db_first, db_only",
        }
    result = await asyncio.to_thread(apply_cutover, mode, force=force, reason=reason)
    status = 200 if result.get("ok") else 409
    return JSONResponse(content=result, status_code=status)
