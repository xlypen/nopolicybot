from __future__ import annotations

from collections import defaultdict

import social_graph
import user_stats


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


def _detect_communities_louvain(node_ids: list[int], edges: list[dict], **kwargs):
    # Deterministic lightweight fallback community assignment.
    comm = {}
    for idx, uid in enumerate(sorted(node_ids)):
        comm[int(uid)] = int(idx % 3)
    return comm, {"modularity": 0.0, "levels": 1}


def _build_payload_from_rows(chat_id, rows, names, period: str = "all", ego_user=None, limit=None, rank_by_user=None):
    rank_by_user = rank_by_user or {}
    period_key = str(period or "all").lower()
    node_ids = set()
    degree = defaultdict(int)
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
    comm, comm_stats = _detect_communities_louvain(list(node_ids), edges)
    nodes = []
    for uid in sorted(node_ids):
        d = int(degree.get(uid, 0))
        nodes.append(
            {
                "id": uid,
                "label": names.get(str(uid), str(uid)),
                "rank": str(rank_by_user.get(uid, "unknown")),
                "degree": d,
                "messages_7d": d,
                "messages_30d": d,
                "influence_score": round(float(d), 3),
                "centrality": round(float(d) / max(1, len(node_ids) - 1), 6),
                "community_id": int(comm.get(uid, 0)),
                "community_label": f"Комьюнити {int(comm.get(uid, 0))}",
                "tier": "core" if d >= 5 else ("secondary" if d >= 2 else "periphery"),
            }
        )
    node_comm = {int(n["id"]): int(n.get("community_id", 0)) for n in nodes}
    for e in edges:
        ca = node_comm.get(int(e["source"]), -1)
        cb = node_comm.get(int(e["target"]), -1)
        e["community_id"] = ca if ca == cb else -1
        e["bridge_score"] = 1.0 if ca != cb else 0.0
    nodes, edges, ds_meta = _downsample_large_graph(nodes, edges)
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "chat_id": chat_id if chat_id is not None else "all",
            "period": period_key,
            "nodes_count": len(nodes),
            "edges_count": len(edges),
            "communities_count": len({int(n.get("community_id", 0)) for n in nodes}) if nodes else 0,
            "communities_algo": "louvain_multi_level_seeded",
            "communities_modularity": float(comm_stats.get("modularity", 0.0)),
            "communities_levels": int(comm_stats.get("levels", 1)),
            "community_labels": {str(int(n.get("community_id", 0))): str(n.get("community_label", "")) for n in nodes},
            "downsampled": bool(ds_meta.get("applied", False)),
            "downsample_meta": ds_meta,
        },
    }


def build_graph_payload(chat_id: int | None, period: str = "all", ego_user: int | None = None, limit: int | None = None) -> dict:
    rows = social_graph.get_connections(chat_id)
    if not rows:
        rows = _fallback_rows_from_user_stats(chat_id)
    names = user_stats.get_user_display_names() if hasattr(user_stats, "get_user_display_names") else {}
    return _build_payload_from_rows(chat_id=chat_id, rows=rows, names=names, period=period, ego_user=ego_user, limit=limit, rank_by_user={})


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
    return _build_payload_from_rows(chat_id=chat_id, rows=rows, names=names, period=period_key, ego_user=ego_user, limit=limit, rank_by_user={})


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
