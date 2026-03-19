import asyncio
import hashlib
import json

from fastapi import APIRouter, Depends, Query

from api.dependencies import require_auth
from services.graph_api import build_graph_payload

router = APIRouter()
_GRAPH_HISTORY: dict[str, dict] = {}
_GRAPH_HISTORY_LOCK = asyncio.Lock()


def _parse_chat_id(chat_id: str) -> int | None:
    raw = str(chat_id or "").strip().lower()
    if raw in ("all", ""):
        return None
    if raw.lstrip("-").isdigit():
        return int(raw)
    return None


def _parse_period(period: str | int) -> int:
    if isinstance(period, int) and 1 <= period <= 90:
        return period
    raw = str(period or "7").strip().lower()
    if raw in ("24h", "1"):
        return 1
    if raw in ("30d", "30"):
        return 30
    if raw in ("7d", "7"):
        return 7
    try:
        n = int(raw.replace("d", ""))
        return max(1, min(90, n))
    except ValueError:
        return 7


def _scope(chat_id: int | None, period: int, ego_user: int | None, limit: int | None) -> str:
    c = "all" if chat_id is None else int(chat_id)
    e = ego_user or 0
    l = limit or 0
    return f"chat={c}|period={period}|ego={e}|limit={l}"


def _edge_id(edge: dict) -> str:
    a = int(edge.get("source", 0) or 0)
    b = int(edge.get("target", 0) or 0)
    if not a and not b:
        return "0|0"
    lo, hi = (a, b) if a <= b else (b, a)
    return f"{int(lo)}|{int(hi)}"


def _graph_version(graph: dict) -> str:
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    node_fp = sorted(
        (
            int(n.get("id", 0) or 0),
            round(float(n.get("influence_score", 0.0) or 0.0), 6),
            round(float(n.get("centrality", 0.0) or 0.0), 6),
            int(n.get("community_id", 0) or 0),
            str(n.get("tier", "") or ""),
        )
        for n in nodes
    )
    edge_fp = sorted(
        (
            _edge_id(e),
            round(float(e.get("weight_period", 0.0) or 0.0), 6),
            round(float(e.get("bridge_score", 0.0) or 0.0), 6),
            int(e.get("community_id", 0) or 0),
        )
        for e in edges
    )
    raw = json.dumps({"nodes": node_fp, "edges": edge_fp}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _graph_delta(prev_graph: dict | None, curr_graph: dict) -> dict:
    prev = prev_graph or {"nodes": [], "edges": [], "meta": {}}
    p_nodes = {int(n.get("id", 0) or 0): n for n in (prev.get("nodes") or []) if int(n.get("id", 0) or 0) != 0}
    c_nodes = {int(n.get("id", 0) or 0): n for n in (curr_graph.get("nodes") or []) if int(n.get("id", 0) or 0) != 0}
    p_edges = {_edge_id(e): e for e in (prev.get("edges") or [])}
    c_edges = {_edge_id(e): e for e in (curr_graph.get("edges") or [])}

    remove_node_ids = [int(uid) for uid in p_nodes.keys() if uid not in c_nodes]
    upsert_nodes = [n for uid, n in c_nodes.items() if uid not in p_nodes or p_nodes.get(uid) != n]
    remove_edge_ids = [eid for eid in p_edges.keys() if eid not in c_edges]
    upsert_edges = [e for eid, e in c_edges.items() if eid not in p_edges or p_edges.get(eid) != e]

    changed = bool(remove_node_ids or upsert_nodes or remove_edge_ids or upsert_edges or (prev.get("meta") or {}) != (curr_graph.get("meta") or {}))
    return {
        "changed": changed,
        "delta": {
            "full_replace": prev_graph is None,
            "remove_node_ids": remove_node_ids,
            "upsert_nodes": upsert_nodes,
            "remove_edge_ids": remove_edge_ids,
            "upsert_edges": upsert_edges,
            "meta": curr_graph.get("meta") or {},
        },
    }


async def _history_get(scope: str) -> dict:
    async with _GRAPH_HISTORY_LOCK:
        payload = _GRAPH_HISTORY.get(scope)
        return payload if isinstance(payload, dict) else {}


async def _history_set(scope: str, version: str, graph: dict) -> None:
    async with _GRAPH_HISTORY_LOCK:
        history = _GRAPH_HISTORY.get(scope)
        if not isinstance(history, dict):
            history = {}
        latest = history.get("latest")
        prev = history.get("prev")
        if isinstance(latest, dict) and latest.get("version") != version:
            prev = latest
        _GRAPH_HISTORY[scope] = {
            "latest": {"version": version, "graph": graph},
            "prev": prev if isinstance(prev, dict) else None,
        }


def _get_graph_payload_compat(chat_id: int | None, period: str, ego_user: int | None, limit: int | None) -> dict:
    """Sync wrapper for build_graph_payload (storage modes, JSON fallback)."""
    return build_graph_payload(chat_id, period=period, ego_user=ego_user, limit=limit)


@router.get("/{chat_id}")
async def get_graph(
    chat_id: str,
    period: str | int = Query(default="7d"),
    ego_user: int | None = Query(default=None),
    limit: int | None = Query(default=None),
    _auth=Depends(require_auth),
):
    cid = _parse_chat_id(chat_id)
    period_str = str(period).strip().lower() if isinstance(period, str) else f"{int(period)}d"
    graph = await asyncio.to_thread(
        _get_graph_payload_compat, cid, period_str, ego_user, limit
    )
    version = _graph_version(graph)
    scope = _scope(cid, _parse_period(period), ego_user, limit)
    await _history_set(scope, version, graph)
    return {"ok": True, "graph": graph, "graph_version": version}


@router.get("/{chat_id}/delta")
async def get_graph_delta(
    chat_id: str,
    period: str | int = Query(default="7d"),
    since: str = Query(default=""),
    ego_user: int | None = Query(default=None),
    limit: int | None = Query(default=None),
    _auth=Depends(require_auth),
):
    cid = _parse_chat_id(chat_id)
    period_str = str(period).strip().lower() if isinstance(period, str) else f"{int(period)}d"
    graph = await asyncio.to_thread(
        _get_graph_payload_compat, cid, period_str, ego_user, limit
    )
    version = _graph_version(graph)
    scope = _scope(cid, _parse_period(period), ego_user, limit)
    history = await _history_get(scope)
    latest = history.get("latest") if isinstance(history, dict) else None
    prev = history.get("prev") if isinstance(history, dict) else None

    prev_graph = None
    if isinstance(latest, dict) and str(latest.get("version") or "") == since:
        prev_graph = latest.get("graph") if isinstance(latest.get("graph"), dict) else None
    elif isinstance(prev, dict) and str(prev.get("version") or "") == since:
        prev_graph = prev.get("graph") if isinstance(prev.get("graph"), dict) else None
    elif isinstance(latest, dict):
        prev_graph = latest.get("graph") if isinstance(latest.get("graph"), dict) else None

    if since and since == version:
        await _history_set(scope, version, graph)
        return {
            "ok": True,
            "changed": False,
            "graph_version": version,
            "delta": {
                "full_replace": False,
                "remove_node_ids": [],
                "upsert_nodes": [],
                "remove_edge_ids": [],
                "upsert_edges": [],
                "meta": graph.get("meta") or {},
            },
        }

    patch = _graph_delta(prev_graph, graph)
    await _history_set(scope, version, graph)
    return {
        "ok": True,
        "changed": bool(patch.get("changed")),
        "graph_version": version,
        "delta": patch.get("delta") or {},
    }
