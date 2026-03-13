from fastapi import APIRouter, Depends, Query

from api.dependencies import get_edge_repo, get_user_repo, require_auth
from services.graph_api import build_payload

router = APIRouter()


@router.get("/{chat_id}")
async def get_graph(
    chat_id: int,
    period: int = Query(default=7, ge=1, le=90),
    edge_repo=Depends(get_edge_repo),
    user_repo=Depends(get_user_repo),
    _auth=Depends(require_auth),
):
    return await build_payload(chat_id, edge_repo, user_repo, period=period)
