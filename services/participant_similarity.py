"""Сходство участника с другими в тех же чатах: профили P-1 (OCEAN), иначе эвристика по рангу и связям."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


def _edge_weight_map(my_connections: list[dict], user_id: int) -> dict[tuple[int, int], int]:
    m: dict[tuple[int, int], int] = {}
    for r in my_connections or []:
        ua = int(r.get("user_a", 0) or 0)
        ub = int(r.get("user_b", 0) or 0)
        peer = ub if ua == int(user_id) else ua
        cid = int(r.get("chat_id", 0) or 0)
        if not peer or peer == int(user_id):
            continue
        key = (cid, peer)
        w = int(r.get("message_count_7d", 0) or 0)
        m[key] = max(m.get(key, 0), w)
    return m


def _peer_name(display_names: dict[str, str], peer_id: int) -> str:
    return str(display_names.get(str(peer_id), "") or "").strip() or f"Участник {peer_id}"


async def _personality_candidates(
    user_id: int,
    chat_ids: list[int],
) -> list[dict[str, Any]]:
    from db.engine import get_db
    from services.personality.comparison import _similarity_interpretation, compare_two
    from services.personality.storage import get_latest_profile, get_profiles_for_chat

    out: list[dict[str, Any]] = []
    uid = int(user_id)
    async with get_db() as session:
        for cid in chat_ids:
            cid = int(cid)
            if cid == 0:
                continue
            ego = await get_latest_profile(session, uid, cid)
            if not ego:
                continue
            profiles = await get_profiles_for_chat(session, cid)
            for pid, pprof in profiles:
                pid = int(pid)
                if pid == uid:
                    continue
                res = compare_two(ego, pprof)
                sim = float(res.similarity_score)
                out.append(
                    {
                        "chat_id": cid,
                        "peer_id": pid,
                        "score": sim,
                        "hint": _similarity_interpretation(sim),
                        "source": "personality",
                    }
                )
    return out


def _heuristic_candidates(
    user_id: int,
    ego_rank: str,
    my_connections: list[dict],
    display_names: dict[str, str],
) -> list[dict[str, Any]]:
    import user_stats

    uid = int(user_id)
    er = str(ego_rank or "unknown").strip().lower()
    seen: set[tuple[int, int]] = set()
    out: list[dict[str, Any]] = []
    for r in my_connections or []:
        ua = int(r.get("user_a", 0) or 0)
        ub = int(r.get("user_b", 0) or 0)
        peer = ub if ua == uid else ua
        cid = int(r.get("chat_id", 0) or 0)
        if not peer or peer == uid or not cid:
            continue
        key = (cid, peer)
        if key in seen:
            continue
        seen.add(key)
        pu = user_stats.get_user(peer, display_names.get(str(peer), ""))
        pr = str(pu.get("rank") or "unknown").strip().lower()
        if pr == er and pr != "unknown":
            rank_sim = 1.0
        elif pr != "unknown" and er != "unknown":
            rank_sim = 0.5
        else:
            rank_sim = 0.4
        m7 = max(0, int(r.get("message_count_7d", 0) or 0))
        activity = min(1.0, math.log(1 + m7) / math.log(1 + 80))
        score = max(0.0, min(1.0, rank_sim * (0.45 + 0.55 * activity)))
        out.append(
            {
                "chat_id": cid,
                "peer_id": peer,
                "score": round(score, 3),
                "hint": "По рангу полит. взглядов и активности переписки (профиль OCEAN ещё не строился).",
                "source": "heuristic",
            }
        )
    return out


def _pick_extremes(
    cands: list[dict[str, Any]],
    wmap: dict[tuple[int, int], int],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not cands:
        return None, None

    def sort_key_most(c: dict[str, Any]) -> tuple[float, int, int, int]:
        w = wmap.get((int(c["chat_id"]), int(c["peer_id"])), 0)
        return (float(c["score"]), w, int(c["peer_id"]), int(c["chat_id"]))

    def sort_key_least(c: dict[str, Any]) -> tuple[float, int, int, int]:
        w = wmap.get((int(c["chat_id"]), int(c["peer_id"])), 0)
        return (float(c["score"]), -w, int(c["peer_id"]), int(c["chat_id"]))

    most = max(cands, key=sort_key_most)
    least = min(cands, key=sort_key_least)
    same = int(most["peer_id"]) == int(least["peer_id"]) and int(most["chat_id"]) == int(least["chat_id"])
    if same and len(cands) == 1:
        return most, None
    if same:
        rest = [c for c in cands if not (int(c["peer_id"]) == int(most["peer_id"]) and int(c["chat_id"]) == int(most["chat_id"]))]
        if not rest:
            return most, None
        least = min(rest, key=sort_key_least)
    return most, least


def build_participant_similarity_peers(
    user_id: int,
    chat_ids: list[int],
    my_connections: list[dict],
    display_names: dict[str, str],
    ego_rank: str,
) -> dict[str, Any]:
    """
    Возвращает словарь для шаблона /me:
    ok, source, most, least (peer_name, chat_id, score, hint), empty_reason, single_peer.
    """
    wmap = _edge_weight_map(my_connections, user_id)
    chats = sorted({int(x) for x in (chat_ids or []) if int(x) != 0})

    cands: list[dict[str, Any]] = []
    try:
        try:
            asyncio.get_running_loop()
            logger.debug("build_participant_similarity_peers: skip async inside running loop")
        except RuntimeError:
            cands = asyncio.run(_personality_candidates(int(user_id), chats))
    except Exception as e:
        logger.warning("participant_similarity personality load: %s", e)
        cands = []

    source = "personality"
    if not cands:
        cands = _heuristic_candidates(int(user_id), ego_rank, my_connections, display_names)
        source = "heuristic"

    if not cands:
        return {
            "ok": False,
            "source": None,
            "most": None,
            "least": None,
            "single_peer": False,
            "empty_reason": "Нет данных для сравнения: нужны связи в чатах и (для точного сравнения) профили личности P-1 по участникам.",
        }

    for c in cands:
        c["peer_name"] = _peer_name(display_names, int(c["peer_id"]))

    most, least = _pick_extremes(cands, wmap)
    single = least is None

    return {
        "ok": True,
        "source": source,
        "most": most,
        "least": least,
        "single_peer": single,
        "empty_reason": "",
    }
