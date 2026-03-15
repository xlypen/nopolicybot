"""Personality API — profile, history, drift, compare, clusters (P-6)."""

from fastapi import APIRouter, Body, Depends, Path, Query

from api.dependencies import get_db_session, require_auth
from services.personality.comparison import (
    ComparisonResult,
    PersonalityCluster,
    cluster_community,
    compare_two,
)
from services.personality.drift import calculate_drift
from services.personality.storage import (
    get_latest_profile,
    get_profile_history,
    get_profiles_for_chat,
)
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


@router.get("/user/{user_id}")
async def get_personality_profile(
    user_id: int = Path(..., ge=1),
    chat_id: int = Query(..., description="Chat ID (required)"),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Get latest personality profile for user in chat."""
    profile = await get_latest_profile(session, user_id, chat_id)
    if not profile:
        return {"ok": False, "error": "Profile not found", "profile": None}
    return {"ok": True, "profile": profile.model_dump(mode="json")}


@router.get("/user/{user_id}/history")
async def get_personality_history(
    user_id: int = Path(..., ge=1),
    chat_id: int = Query(..., description="Chat ID"),
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Get profile history for drift analysis."""
    profiles = await get_profile_history(session, user_id, chat_id, limit=limit)
    return {
        "ok": True,
        "user_id": user_id,
        "chat_id": chat_id,
        "profiles": [p.model_dump(mode="json") for p in profiles],
    }


@router.get("/user/{user_id}/drift")
async def get_personality_drift(
    user_id: int = Path(..., ge=1),
    chat_id: int = Query(..., description="Chat ID"),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Get drift between latest two profiles."""
    drift = await calculate_drift(session, user_id, chat_id, emit_alert=False)
    if not drift:
        return {"ok": False, "error": "Need at least 2 profiles for drift", "drift": None}
    return {"ok": True, "drift": drift.model_dump()}


@router.post("/compare")
async def post_compare(
    body: dict = Body(default_factory=dict),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """
    Compare two users' personality profiles.
    Body: { "user_id_a": int, "user_id_b": int, "chat_id": int }
    """
    ua = body.get("user_id_a")
    ub = body.get("user_id_b")
    chat_id = body.get("chat_id")
    if ua is None or ub is None or chat_id is None:
        return {"ok": False, "error": "Need user_id_a, user_id_b, chat_id"}
    try:
        ua, ub, chat_id = int(ua), int(ub), int(chat_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid user_id or chat_id"}

    pa = await get_latest_profile(session, ua, chat_id)
    pb = await get_latest_profile(session, ub, chat_id)
    if not pa:
        return {"ok": False, "error": f"Profile not found for user {ua}"}
    if not pb:
        return {"ok": False, "error": f"Profile not found for user {ub}"}

    result: ComparisonResult = compare_two(pa, pb)
    return {
        "ok": True,
        "comparison": {
            "user_id_a": result.user_id_a,
            "user_id_b": result.user_id_b,
            "username_a": result.username_a,
            "username_b": result.username_b,
            "ocean_deltas": result.ocean_deltas,
            "similarity_score": result.similarity_score,
            "most_similar_dimensions": result.most_similar_dimensions,
            "most_different_dimensions": result.most_different_dimensions,
        },
    }


@router.get("/community/{chat_id}/clusters")
async def get_community_clusters(
    chat_id: int = Path(..., description="Chat ID"),
    n_clusters: int | None = Query(default=None, ge=2, le=20),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Get personality-based clusters for chat community."""
    profiles = await get_profiles_for_chat(session, chat_id)
    if len(profiles) < 2:
        return {"ok": True, "chat_id": chat_id, "clusters": [], "message": "Need at least 2 profiles"}

    clusters: list[PersonalityCluster] = cluster_community(profiles, n_clusters=n_clusters)
    return {
        "ok": True,
        "chat_id": chat_id,
        "clusters": [
            {
                "cluster_id": c.cluster_id,
                "user_ids": c.user_ids,
                "usernames": {str(k): v for k, v in c.usernames.items()},
                "centroid_ocean": c.centroid_ocean,
                "size": c.size,
            }
            for c in clusters
        ],
    }
