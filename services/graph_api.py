from __future__ import annotations

import asyncio
from collections import defaultdict

import social_graph
import user_stats
from db.engine import get_db
from db.repositories.edge_repo import EdgeRepository
from db.repositories.user_repo import UserRepository
from services.storage_cutover import get_storage_mode

try:
    import networkx as nx
except Exception:  # pragma: no cover - optional runtime dependency fallback
    nx = None


def _downsample_large_graph(nodes: list[dict], edges: list[dict], max_nodes: int = 320, max_edges: int = 900):
    if len(nodes) <= max_nodes and len(edges) <= max_edges:
        return nodes, edges, {"applied": False, "original_nodes": len(nodes), "original_edges": len(edges)}
    ranked = sorted(nodes, key=lambda n: float(n.get("influence_score", 0.0) or 0.0), reverse=True)
    kept_nodes = ranked[:max_nodes]
    kept_ids = {int(n.get("id", 0) or 0) for n in kept_nodes}
    kept_edges = [e for e in edges if int(e.get("source", 0) or 0) in kept_ids and int(e.get("target", 0) or 0) in kept_ids][:max_edges]
    return kept_nodes, kept_edges, {
        "applied": True,
        "original_nodes": len(nodes),
        "original_edges": len(edges),
        "kept_nodes": len(kept_nodes),
        "kept_edges": len(kept_edges),
    }


def _fallback_graph_analytics(node_ids: list[int], edges: list[dict]) -> dict:
    node_sorted = sorted({int(x) for x in (node_ids or []) if int(x) != 0})
    degree = defaultdict(int)
    for e in edges or []:
        a = int(e.get("source", 0) or 0)
        b = int(e.get("target", 0) or 0)
        if not a or not b or a == b:
            continue
        degree[a] += 1
        degree[b] += 1
    comm = {}
    for idx, uid in enumerate(node_sorted):
        comm[int(uid)] = int(idx % 3)
    n = max(1, len(node_sorted) - 1)
    centrality = {uid: round(float(degree.get(uid, 0)) / float(n), 6) for uid in node_sorted}
    influence = {uid: round(float(centrality.get(uid, 0.0)), 6) for uid in node_sorted}
    return {
        "communities": comm,
        "centrality": centrality,
        "influence": influence,
        "edge_bridge": {},
        "stats": {"algo": "builtin_fallback_round_robin", "modularity": 0.0, "levels": 1},
    }


def _compute_graph_analytics(node_ids: list[int], edges: list[dict]) -> dict:
    if nx is None:
        return _fallback_graph_analytics(node_ids, edges)
    try:
        g = nx.Graph()
        node_sorted = sorted({int(x) for x in (node_ids or []) if int(x) != 0})
        g.add_nodes_from(node_sorted)
        for e in edges or []:
            a = int(e.get("source", 0) or 0)
            b = int(e.get("target", 0) or 0)
            if not a or not b or a == b:
                continue
            w = float(e.get("weight_period", e.get("weight", 0.0)) or 0.0)
            if w <= 0:
                w = 1e-6
            if g.has_edge(a, b):
                g[a][b]["weight"] = float(g[a][b].get("weight", 0.0)) + w
            else:
                g.add_edge(a, b, weight=w)

        if g.number_of_nodes() <= 1:
            return _fallback_graph_analytics(node_sorted, edges)

        degree_centrality = nx.degree_centrality(g)
        if g.number_of_edges() > 0:
            pagerank = nx.pagerank(g, weight="weight")
            comm_raw = list(nx.algorithms.community.greedy_modularity_communities(g, weight="weight"))
            edge_betweenness = nx.edge_betweenness_centrality(g, normalized=True, weight="weight")
        else:
            pagerank = {uid: 0.0 for uid in g.nodes}
            comm_raw = [{uid} for uid in g.nodes]
            edge_betweenness = {}

        if not comm_raw:
            comm_raw = [{uid} for uid in g.nodes]

        comm_sorted = sorted(
            [sorted(int(uid) for uid in members) for members in comm_raw],
            key=lambda members: (-len(members), members[0] if members else 0),
        )
        communities: dict[int, int] = {}
        for idx, members in enumerate(comm_sorted):
            for uid in members:
                communities[int(uid)] = int(idx)

        modularity = 0.0
        try:
            modularity = float(nx.algorithms.community.quality.modularity(g, [set(x) for x in comm_sorted], weight="weight"))
        except Exception:
            modularity = 0.0

        edge_bridge = {}
        for pair, score in (edge_betweenness or {}).items():
            a, b = int(pair[0]), int(pair[1])
            edge_bridge[(min(a, b), max(a, b))] = float(score or 0.0)

        return {
            "communities": communities,
            "centrality": {int(uid): float(degree_centrality.get(uid, 0.0)) for uid in g.nodes},
            "influence": {int(uid): float(pagerank.get(uid, 0.0)) for uid in g.nodes},
            "edge_bridge": edge_bridge,
            "stats": {"algo": "networkx_greedy_modularity", "modularity": float(modularity), "levels": 1},
        }
    except Exception:
        return _fallback_graph_analytics(node_ids, edges)


def _build_payload_from_rows(chat_id, rows, names, period: str = "all", ego_user=None, limit=None, rank_by_user=None):
    rank_by_user = rank_by_user or {}
    period_key = str(period or "all").lower()
    node_ids = set()
    degree = defaultdict(int)
    messages_7d = defaultdict(float)
    messages_30d = defaultdict(float)
    edges = []
    for r in rows or []:
        a = int(r.get("user_a", 0) or 0)
        b = int(r.get("user_b", 0) or 0)
        if not a or not b:
            continue
        w_all = float(r.get("message_count_total", r.get("message_count", 0)) or 0.0)
        w = w_all
        if period_key in {"24h", "1"}:
            w = float(r.get("message_count_24h", 0) or 0.0)
        elif period_key in {"7d", "7"}:
            w = float(r.get("message_count_7d", 0) or 0.0)
        elif period_key in {"30d", "30"}:
            w = float(r.get("message_count_30d", 0) or 0.0)
        if w <= 0:
            continue
        node_ids.add(a)
        node_ids.add(b)
        degree[a] += 1
        degree[b] += 1
        m7 = float(r.get("message_count_7d", w_all) or 0.0)
        m30 = float(r.get("message_count_30d", w_all) or 0.0)
        messages_7d[a] += m7
        messages_7d[b] += m7
        messages_30d[a] += m30
        messages_30d[b] += m30
        edges.append(
            {
                "source": a,
                "target": b,
                "weight": float(w_all),
                "weight_period": float(w),
                "message_count_a_to_b": int(r.get("message_count_a_to_b", 0) or 0),
                "message_count_b_to_a": int(r.get("message_count_b_to_a", 0) or 0),
                "bridge_score": 0.0,
                "community_id": 0,
                "arrow_to": None,
            }
        )
    if limit:
        edges.sort(key=lambda e: float(e.get("weight_period", 0.0)), reverse=True)
        edges = edges[: max(1, int(limit))]
    analytics = _compute_graph_analytics(list(node_ids), edges)
    comm = analytics.get("communities") or {}
    comm_stats = analytics.get("stats") or {}
    node_centrality = analytics.get("centrality") or {}
    node_influence = analytics.get("influence") or {}
    edge_bridge = analytics.get("edge_bridge") or {}
    nodes = []
    for uid in sorted(node_ids):
        d = int(degree.get(uid, 0))
        centrality = float(node_centrality.get(uid, 0.0) or 0.0)
        influence = float(node_influence.get(uid, centrality) or centrality)
        tier = "core" if centrality >= 0.35 or d >= 5 else ("secondary" if centrality >= 0.15 or d >= 2 else "periphery")
        nodes.append(
            {
                "id": uid,
                "label": names.get(str(uid), str(uid)),
                "rank": str(rank_by_user.get(uid, "unknown")),
                "degree": d,
                "messages_7d": int(round(float(messages_7d.get(uid, 0.0) or 0.0))),
                "messages_30d": int(round(float(messages_30d.get(uid, 0.0) or 0.0))),
                "influence_score": round(influence, 6),
                "centrality": round(centrality, 6),
                "community_id": int(comm.get(uid, 0)),
                "community_label": f"Комьюнити {int(comm.get(uid, 0))}",
                "tier": tier,
            }
        )
    node_comm = {int(n["id"]): int(n.get("community_id", 0)) for n in nodes}
    for e in edges:
        ca = node_comm.get(int(e["source"]), -1)
        cb = node_comm.get(int(e["target"]), -1)
        key = (min(int(e["source"]), int(e["target"])), max(int(e["source"]), int(e["target"])))
        bridge_score = float(edge_bridge.get(key, 0.0) or 0.0)
        if ca != cb:
            bridge_score = max(bridge_score, 0.75)
        e["community_id"] = ca if ca == cb else -1
        e["bridge_score"] = round(bridge_score, 6)
    nodes, edges, ds_meta = _downsample_large_graph(nodes, edges)
    comm_algo = str(comm_stats.get("algo", "builtin_fallback_round_robin") or "builtin_fallback_round_robin")
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "chat_id": chat_id if chat_id is not None else "all",
            "period": period_key,
            "nodes_count": len(nodes),
            "edges_count": len(edges),
            "communities_count": len({int(n.get("community_id", 0)) for n in nodes}) if nodes else 0,
            "communities_algo": comm_algo,
            "communities_modularity": float(comm_stats.get("modularity", 0.0)),
            "communities_levels": int(comm_stats.get("levels", 1)),
            "community_labels": {str(int(n.get("community_id", 0))): str(n.get("community_label", "")) for n in nodes},
            "graph_engine": "networkx" if comm_algo.startswith("networkx") else "builtin",
            "downsampled": bool(ds_meta.get("applied", False)),
            "downsample_meta": ds_meta,
        },
    }


def build_graph_payload(chat_id: int | None, period: str = "all", ego_user: int | None = None, limit: int | None = None) -> dict:
    mode = get_storage_mode()
    if chat_id is not None and mode in {"db", "hybrid"}:
        try:
            db_payload = asyncio.run(_build_graph_payload_from_db(chat_id, period=period, ego_user=ego_user, limit=limit))
            has_graph = bool((db_payload.get("nodes") or []) or (db_payload.get("edges") or []))
            if has_graph or mode == "db":
                return db_payload
        except Exception:
            if mode == "db":
                return {"nodes": [], "edges": [], "meta": {"chat_id": chat_id, "period": str(period or "all"), "source": "db_error"}}

    rows = social_graph.get_connections(chat_id)
    if not rows:
        rows = _fallback_rows_from_user_stats(chat_id)
    names = user_stats.get_user_display_names() if hasattr(user_stats, "get_user_display_names") else {}
    payload = _build_payload_from_rows(chat_id=chat_id, rows=rows, names=names, period=period, ego_user=ego_user, limit=limit, rank_by_user={})
    payload.setdefault("meta", {})["source"] = "json"
    return payload


async def build_payload(chat_id: int, edge_repo, user_repo, period: int = 7, ego_user: int | None = None, limit: int | None = None) -> dict:
    edge_models = await edge_repo.get_all(chat_id)
    user_models = await user_repo.get_all(chat_id)
    names = {}
    for u in user_models or []:
        uid = int(getattr(u, "id", 0) or 0)
        if uid:
            first = str(getattr(u, "first_name", "") or "").strip()
            username = str(getattr(u, "username", "") or "").strip()
            names[str(uid)] = first or username or str(uid)
    rows = [
        {
            "user_a": int(getattr(e, "from_user", 0) or 0),
            "user_b": int(getattr(e, "to_user", 0) or 0),
            "message_count_total": float(getattr(e, "weight", 0.0) or 0.0),
            "message_count_7d": float(getattr(e, "period_7d", 0.0) or 0.0),
            "message_count_30d": float(getattr(e, "period_30d", 0.0) or 0.0),
            "message_count_a_to_b": int(getattr(e, "weight", 0.0) or 0.0),
            "message_count_b_to_a": int(getattr(e, "weight", 0.0) or 0.0),
        }
        for e in edge_models or []
    ]
    period_key = "7d" if int(period) == 7 else ("30d" if int(period) == 30 else ("24h" if int(period) == 1 else "all"))
    payload = _build_payload_from_rows(chat_id=chat_id, rows=rows, names=names, period=period_key, ego_user=ego_user, limit=limit, rank_by_user={})
    payload.setdefault("meta", {})["source"] = "db"
    return payload


async def _build_graph_payload_from_db(chat_id: int, period: str = "all", ego_user: int | None = None, limit: int | None = None) -> dict:
    period_key = str(period or "all").lower()
    period_days = 7
    if period_key in {"24h", "1"}:
        period_days = 1
    elif period_key in {"30d", "30"}:
        period_days = 30
    elif period_key in {"7d", "7"}:
        period_days = 7
    else:
        period_days = 7

    async with get_db() as session:
        edge_repo = EdgeRepository(session)
        user_repo = UserRepository(session)
        payload = await build_payload(
            int(chat_id),
            edge_repo,
            user_repo,
            period=period_days,
            ego_user=ego_user,
            limit=limit,
        )
    return payload


def _fallback_rows_from_user_stats(chat_id: int | None) -> list[dict]:
    """
    Recovery fallback: build weak interaction edges from message sequences
    when explicit social_graph edges are absent.
    """
    data = user_stats._load() if hasattr(user_stats, "_load") else {}
    users = data.get("users", {}) or {}
    target = str(chat_id) if chat_id is not None else None

    interactions: dict[tuple[int, int], int] = defaultdict(int)
    for uid_raw, u in users.items():
        try:
            uid = int(uid_raw)
        except Exception:
            continue
        by_chat = u.get("messages_by_chat") or {}
        for cid, msgs in by_chat.items():
            if target is not None and str(cid) != target:
                continue
            if not isinstance(msgs, list) or not msgs:
                continue
            # Use neighboring users in the same chat as weak interaction signal.
            other_users = []
            for ou_raw, ou in users.items():
                try:
                    ouid = int(ou_raw)
                except Exception:
                    continue
                if ouid == uid:
                    continue
                ou_by_chat = ou.get("messages_by_chat") or {}
                if (ou_by_chat.get(str(cid)) or []):
                    other_users.append(ouid)
            if not other_users:
                continue
            step = max(1, len(other_users) // max(1, min(8, len(msgs))))
            for i in range(0, len(msgs), step):
                ouid = other_users[(i // step) % len(other_users)]
                a, b = (uid, ouid) if uid < ouid else (ouid, uid)
                interactions[(a, b)] += 1

    rows: list[dict] = []
    for (a, b), w in interactions.items():
        rows.append(
            {
                "user_a": int(a),
                "user_b": int(b),
                "message_count_total": int(w),
                "message_count_24h": 0,
                "message_count_7d": int(w),
                "message_count_30d": int(w),
                "message_count_a_to_b": int(w // 2),
                "message_count_b_to_a": int(w - (w // 2)),
                "tone": "neutral",
                "topics": [],
                "tone_trend": "stable",
            }
        )
    return rows
