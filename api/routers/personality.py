"""Personality API — profile, history, drift, compare, clusters, portrait generation."""

import asyncio
import logging

from fastapi import APIRouter, Body, Depends, Path, Query
from fastapi.responses import Response

from api.dependencies import get_db_session, require_auth
from services.personality.comparison import (
    ComparisonResult,
    PersonalityCluster,
    build_ocean_narrative_paragraphs,
    build_ocean_verbal_summary,
    cluster_community,
    compare_two,
)
from services.personality.drift import calculate_drift, calculate_drift_sync
from services.personality.storage import (
    get_latest_profile,
    get_profile_history,
    get_profiles_for_chat,
)
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
_logger = logging.getLogger(__name__)

_portrait_generating: set[str] = set()


def _parse_chat_id(raw: int | str) -> int:
    """Parse chat_id: 'all' or 0 -> 0 (все чаты), иначе int."""
    if raw == 0 or str(raw).strip().lower() == "all":
        return 0
    return int(raw)


@router.get("/user/{user_id}")
async def get_personality_profile(
    user_id: int = Path(..., ge=1),
    chat_id: int | str = Query(..., description="Chat ID or 'all' for aggregate"),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Get latest personality profile for user in chat."""
    cid = _parse_chat_id(chat_id)
    profile = await get_latest_profile(session, user_id, cid)
    if not profile:
        return {"ok": False, "error": "Profile not found", "profile": None}
    return {"ok": True, "profile": profile.model_dump(mode="json")}


@router.get("/user/{user_id}/history")
async def get_personality_history(
    user_id: int = Path(..., ge=1),
    chat_id: int | str = Query(..., description="Chat ID or 'all'"),
    limit: int = Query(default=10, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Get profile history for drift analysis."""
    cid = _parse_chat_id(chat_id)
    profiles = await get_profile_history(session, user_id, cid, limit=limit)
    return {
        "ok": True,
        "user_id": user_id,
        "chat_id": cid,
        "profiles": [p.model_dump(mode="json") for p in profiles],
    }


@router.get("/user/{user_id}/verify")
async def get_personality_verify(
    user_id: int = Path(..., ge=1),
    chat_id: int | str = Query(..., description="Chat ID or 'all'"),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Verify profile against observed behavior (P-9)."""
    from services.personality.storage import get_latest_profile
    from services.personality.verification import (
        BehavioralSignals,
        VerificationResult,
        compute_behavioral_signals,
        verify_profile,
    )
    from services.marketing_metrics import get_user_metrics
    from user_stats import get_user_messages_archive

    cid = _parse_chat_id(chat_id)
    profile = await get_latest_profile(session, user_id, cid)
    if not profile:
        return {"ok": False, "error": "Profile not found", "verification": None}

    messages = await asyncio.to_thread(get_user_messages_archive, user_id, None if cid == 0 else cid)
    metrics = await asyncio.to_thread(get_user_metrics, user_id, chat_id=None if cid == 0 else cid, days=30)

    behavior = compute_behavioral_signals(messages, metrics)
    verification: VerificationResult = verify_profile(profile, behavior)

    return {
        "ok": True,
        "verification": {
            "correlation_score": verification.correlation_score,
            "reliability_badge": verification.reliability_badge,
            "matched_dimensions": verification.matched_dimensions,
            "mismatched_dimensions": verification.mismatched_dimensions,
        },
        "behavior": {
            "message_count": behavior.message_count,
            "conflict_ratio": round(behavior.conflict_ratio, 3),
            "avg_message_length": round(behavior.avg_message_length, 1),
        },
    }


@router.get("/user/{user_id}/drift")
async def get_personality_drift(
    user_id: int = Path(..., ge=1),
    chat_id: int | str = Query(..., description="Chat ID or 'all'"),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Get drift between latest two profiles."""
    cid = _parse_chat_id(chat_id)
    drift = await calculate_drift(session, user_id, cid, emit_alert=False)
    if not drift:
        return {"ok": False, "error": "Need at least 2 profiles for drift", "drift": None}
    return {"ok": True, "drift": drift.model_dump()}


@router.get("/user/{user_id}/drift-history")
async def get_drift_history(
    user_id: int = Path(..., ge=1),
    chat_id: int | str = Query(..., description="Chat ID or 'all'"),
    days: int = Query(default=90, ge=7, le=365),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Return drift score timeline from profile history."""
    cid = _parse_chat_id(chat_id)
    profiles = await get_profile_history(session, user_id, cid, limit=20)
    if len(profiles) < 2:
        return {"ok": True, "user_id": user_id, "chat_id": cid, "points": []}

    # Profiles come newest-first; reverse to chronological order for pairing
    profiles_chrono = list(reversed(profiles))
    points = []
    for i in range(1, len(profiles_chrono)):
        prev, curr = profiles_chrono[i - 1], profiles_chrono[i]
        drift = calculate_drift_sync(
            [curr, prev],
            user_id=str(user_id),
            chat_id=str(cid),
        )
        if drift is None:
            continue
        points.append({
            "date": curr.generated_at[:10] if curr.generated_at else "",
            "drift_score": drift.drift_score,
            "significant_changes": drift.significant_changes,
            "alert": drift.alert,
            "alert_reason": drift.alert_reason,
        })

    return {"ok": True, "user_id": user_id, "chat_id": cid, "points": points}


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
    verbal = build_ocean_verbal_summary(
        result.username_a or str(ua),
        result.username_b or str(ub),
        result.ocean_deltas,
    )
    narrative = build_ocean_narrative_paragraphs(
        result.username_a or str(ua),
        result.username_b or str(ub),
        result.ocean_deltas,
        result.similarity_score,
        result.most_similar_dimensions,
        result.most_different_dimensions,
    )
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
            "ocean_verbal_lines": verbal,
            "ocean_narrative_paragraphs": narrative,
        },
    }


@router.post("/build")
async def post_build_profile(
    body: dict = Body(default_factory=dict),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """
    Build and save structured personality profile from messages.
    Body: { "user_id": int, "chat_id": int }
    """
    from services.personality.ensemble import build_ensemble_profile
    from services.personality.storage import save_profile
    from user_stats import get_user, get_user_messages_archive

    uid = body.get("user_id")
    chat_id_raw = body.get("chat_id")
    if uid is None or chat_id_raw is None:
        return {"ok": False, "error": "Need user_id, chat_id"}
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid user_id"}
    if str(chat_id_raw).strip().lower() == "all":
        chat_id = 0  # 0 = все чаты
    else:
        try:
            chat_id = int(chat_id_raw)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Invalid chat_id"}

    messages = await asyncio.to_thread(get_user_messages_archive, uid, None if chat_id == 0 else chat_id)
    if not messages:
        return {"ok": False, "error": "No messages in archive"}

    u = await asyncio.to_thread(get_user, uid)
    username = str(u.get("display_name") or uid)

    profile = await asyncio.to_thread(
        build_ensemble_profile,
        messages=messages,
        user_id=uid,
        username=username,
        period_days=30,
        chat_description="Telegram chat",
    )
    if not profile:
        return {"ok": False, "error": "Profile build failed"}

    await save_profile(session, uid, chat_id, profile)
    await session.commit()
    return {"ok": True, "messages_analyzed": len(messages), "profile": profile.model_dump(mode="json")}


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


# ---------------------------------------------------------------------------
# IMG-3/4/5: Portrait generation endpoints
# ---------------------------------------------------------------------------

@router.post("/user/{user_id}/portrait/generate")
async def post_generate_portrait(
    user_id: int = Path(..., ge=1),
    body: dict = Body(default_factory=dict),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """
    Generate visual portrait from personality profile.
    Body: { chat_id: int|"all", style_variant?: str, provider?: str }
    """
    from services.personality.image_prompt_builder import build_image_prompt, STYLE_VARIANTS
    from services.personality.image_generator import generate_image_from_prompt
    from services.personality.portrait_storage import (
        save_portrait_file, save_portrait_record, compute_hash,
    )
    from services.portrait_image import PORTRAIT_IMAGES_DIR

    chat_id_raw = body.get("chat_id", "all")
    chat_id = _parse_chat_id(chat_id_raw)
    style_variant = str(body.get("style_variant", "concept_art") or "concept_art")
    if style_variant not in STYLE_VARIANTS:
        style_variant = "concept_art"
    provider = body.get("provider")

    key = f"{user_id}:{chat_id}:{style_variant}"
    if key in _portrait_generating:
        return {"ok": False, "error": "Generation already in progress"}

    profile = await get_latest_profile(session, user_id, chat_id)
    if not profile:
        profile = await get_latest_profile(session, user_id, 0)
    if not profile:
        return {"ok": False, "error": "No personality profile found. Build one first."}

    prompt_data = build_image_prompt(profile, style_variant=style_variant)

    _portrait_generating.add(key)
    try:
        result = await asyncio.to_thread(
            generate_image_from_prompt,
            prompt_data["positive_prompt"],
            prompt_data["negative_prompt"],
            preferred_provider=provider,
        )
    finally:
        _portrait_generating.discard(key)

    if not result:
        return {"ok": False, "error": "All image generation models failed. Check API keys."}

    image_bytes = result["image_bytes"]
    image_hash = compute_hash(image_bytes)
    image_path = save_portrait_file(chat_id, user_id, image_bytes)

    PORTRAIT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    legacy_path = PORTRAIT_IMAGES_DIR / f"{user_id}.png"
    legacy_path.write_bytes(image_bytes)

    profile_row_id = None
    try:
        from db.models import PersonalityProfileRow
        from sqlalchemy import select
        stmt = (
            select(PersonalityProfileRow.id)
            .where(
                PersonalityProfileRow.user_id == user_id,
                PersonalityProfileRow.chat_id == chat_id,
            )
            .order_by(PersonalityProfileRow.generated_at.desc())
            .limit(1)
        )
        r = await session.execute(stmt)
        profile_row_id = r.scalar_one_or_none()
    except Exception:
        pass

    portrait_id = await save_portrait_record(
        session,
        user_id=user_id,
        chat_id=chat_id,
        profile_id=profile_row_id,
        model_used=result["model_used"],
        prompt_used=prompt_data["positive_prompt"],
        seed_description=prompt_data["seed_description"],
        generation_time_sec=result["generation_time_sec"],
        image_path=image_path,
        image_hash=image_hash,
        style_variant=style_variant,
    )
    await session.commit()

    return {
        "ok": True,
        "portrait_id": portrait_id,
        "model_used": result["model_used"],
        "provider": result["provider"],
        "style_variant": style_variant,
        "generation_time_sec": result["generation_time_sec"],
        "seed_description": prompt_data["seed_description"],
        "cost": "free" if result["provider"] in ("huggingface", "gemini") else "paid",
    }


@router.get("/user/{user_id}/portrait/latest")
async def get_latest_portrait_endpoint(
    user_id: int = Path(..., ge=1),
    chat_id: int | str = Query(default="all"),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Get latest portrait metadata for user."""
    from services.personality.portrait_storage import get_latest_portrait
    cid = _parse_chat_id(chat_id)
    portrait = await get_latest_portrait(session, user_id, cid)
    if not portrait:
        return {"ok": False, "error": "No portrait found"}
    return {"ok": True, "portrait": portrait}


@router.get("/user/{user_id}/portrait/history")
async def get_portrait_history_endpoint(
    user_id: int = Path(..., ge=1),
    chat_id: int | str = Query(default="all"),
    limit: int = Query(default=20, ge=1, le=50),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Get portrait generation history."""
    from services.personality.portrait_storage import get_portrait_history
    cid = _parse_chat_id(chat_id)
    portraits = await get_portrait_history(session, user_id, cid, limit=limit)
    return {"ok": True, "portraits": portraits}


@router.get("/user/{user_id}/portrait/{portrait_id}")
async def get_portrait_by_id_endpoint(
    user_id: int = Path(..., ge=1),
    portrait_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Get specific portrait metadata."""
    from services.personality.portrait_storage import get_portrait_by_id
    portrait = await get_portrait_by_id(session, portrait_id)
    if not portrait or portrait["user_id"] != user_id:
        return {"ok": False, "error": "Portrait not found"}
    return {"ok": True, "portrait": portrait}


@router.get("/user/{user_id}/portrait/{portrait_id}/image")
async def get_portrait_image_file(
    user_id: int = Path(..., ge=1),
    portrait_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """Serve actual portrait image file."""
    from pathlib import Path as PPath
    from services.personality.portrait_storage import get_portrait_by_id, PORTRAITS_DIR
    portrait = await get_portrait_by_id(session, portrait_id)
    if not portrait or portrait["user_id"] != user_id:
        return Response(status_code=404)
    image_path = PPath(portrait["image_path"])
    if not image_path.is_absolute():
        image_path = PORTRAITS_DIR.parent.parent / image_path
    if not image_path.exists():
        return Response(status_code=404)
    return Response(
        content=image_path.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/styles")
async def get_style_variants(_auth=Depends(require_auth)):
    """List available style variants for portrait generation."""
    from services.personality.image_prompt_builder import STYLE_VARIANTS
    return {
        "ok": True,
        "styles": {
            k: {"description": v["description"]}
            for k, v in STYLE_VARIANTS.items()
        },
    }


@router.get("/credits-status")
async def get_credits_status(_auth=Depends(require_auth)):
    """Check if OpenRouter credits are available or exhausted."""
    import os
    from ai.client import is_credits_exhausted, prefer_free_mode

    has_hf = bool((os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or "").strip())
    has_gemini = bool((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())
    has_replicate = bool((os.getenv("REPLICATE_API_TOKEN") or "").strip())
    exhausted = is_credits_exhausted()
    prefer_free = prefer_free_mode()
    effective_mode = "free" if (exhausted or prefer_free) else "paid"
    return {
        "ok": True,
        "openrouter_credits_exhausted": exhausted,
        "prefer_free_mode": prefer_free,
        "free_providers": {
            "huggingface": has_hf,
            "gemini": has_gemini,
            "replicate": has_replicate,
        },
        "mode": effective_mode,
        "mode_label": "Gemini (бесплатно)" if prefer_free else ("free (fallback)" if exhausted else "OpenRouter (платно)"),
    }


@router.post("/compare/portrait")
async def post_compare_portrait(
    body: dict = Body(default_factory=dict),
    session: AsyncSession = Depends(get_db_session),
    _auth=Depends(require_auth),
):
    """
    Generate comparison diptych of two users' portraits.
    Body: { user_id_a: int, user_id_b: int, chat_id: int|"all", style_variant?: str }
    """
    from services.personality.portrait_comparison import generate_comparison_diptych
    from services.personality.portrait_storage import save_portrait_file, compute_hash

    ua = body.get("user_id_a")
    ub = body.get("user_id_b")
    chat_id_raw = body.get("chat_id", "all")
    style_variant = str(body.get("style_variant", "concept_art") or "concept_art")

    if ua is None or ub is None:
        return {"ok": False, "error": "Need user_id_a and user_id_b"}
    try:
        ua, ub = int(ua), int(ub)
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid user_id"}

    chat_id = _parse_chat_id(chat_id_raw)

    pa = await get_latest_profile(session, ua, chat_id)
    pb = await get_latest_profile(session, ub, chat_id)
    if not pa:
        pa = await get_latest_profile(session, ua, 0)
    if not pb:
        pb = await get_latest_profile(session, ub, 0)
    if not pa:
        return {"ok": False, "error": f"No profile for user {ua}"}
    if not pb:
        return {"ok": False, "error": f"No profile for user {ub}"}

    result = await asyncio.to_thread(
        generate_comparison_diptych, pa, pb, style_variant=style_variant,
    )
    if not result:
        return {"ok": False, "error": "Diptych generation failed"}

    import base64
    image_b64 = base64.b64encode(result["image_bytes"]).decode("ascii")

    return {
        "ok": True,
        "diptych_base64": image_b64,
        "model_a": result["model_a"],
        "model_b": result["model_b"],
        "seed_a": result["seed_a"],
        "seed_b": result["seed_b"],
        "generation_time_sec": result["generation_time_sec"],
    }
