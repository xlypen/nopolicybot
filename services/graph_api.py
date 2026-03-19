from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from collections import defaultdict

import social_graph
import user_stats
from db.engine import get_db
from db.repositories.edge_repo import EdgeRepository
from db.repositories.user_repo import UserRepository
from services.storage_cutover import (
    get_storage_mode,
    storage_db_only_mode,
    storage_db_reads_enabled,
    storage_json_fallback_enabled,
)

try:
    import networkx as nx
except Exception:  # pragma: no cover - optional runtime dependency fallback
    nx = None

WEBGL_HINT_NODE_THRESHOLD = 260
WEBGL_HINT_EDGE_THRESHOLD = 1100


def _downsample_large_graph(nodes: list[dict], edges: list[dict], max_nodes: int = 320, max_edges: int = 900):
    if len(nodes) <= max_nodes and len(edges) <= max_edges:
        return nodes, edges, {"applied": False, "original_nodes": len(nodes), "original_edges": len(edges)}
    ranked = sorted(nodes, key=lambda n: float(n.get("influence_score", 0.0) or 0.0), reverse=True)
    node_by_id = {int(n.get("id", 0) or 0): n for n in nodes if int(n.get("id", 0) or 0) != 0}

    # Preserve bridge endpoints first so community connectors do not disappear.
    bridge_ids: set[int] = set()
    for e in edges or []:
        s = int(e.get("source", 0) or 0)
        t = int(e.get("target", 0) or 0)
        if not s or not t:
            continue
        is_bridge = float(e.get("bridge_score", 0.0) or 0.0) > 0.0 or int(e.get("community_id", 0) or 0) == -1
        if is_bridge:
            bridge_ids.add(s)
            bridge_ids.add(t)

    bridge_ranked = sorted(
        [node_by_id[x] for x in bridge_ids if x in node_by_id],
        key=lambda n: float(n.get("influence_score", 0.0) or 0.0),
        reverse=True,
    )
    ordered_candidates = []
    seen_ids = set()
    for n in bridge_ranked + ranked:
        nid = int(n.get("id", 0) or 0)
        if not nid or nid in seen_ids:
            continue
        seen_ids.add(nid)
        ordered_candidates.append(n)

    kept_nodes = ordered_candidates[: max(1, int(max_nodes))]
    kept_ids = {int(n.get("id", 0) or 0) for n in kept_nodes}

    intra_edges = [e for e in edges if int(e.get("source", 0) or 0) in kept_ids and int(e.get("target", 0) or 0) in kept_ids]
    intra_edges.sort(
        key=lambda e: (
            1 if (float(e.get("bridge_score", 0.0) or 0.0) > 0.0 or int(e.get("community_id", 0) or 0) == -1) else 0,
            float(e.get("weight_period", e.get("weight", 0.0)) or 0.0),
        ),
        reverse=True,
    )
    kept_edges = intra_edges[: max(1, int(max_edges))]
    kept_bridge_ids = {int(n.get("id", 0) or 0) for n in kept_nodes if int(n.get("id", 0) or 0) in bridge_ids}
    return kept_nodes, kept_edges, {
        "applied": True,
        "original_nodes": len(nodes),
        "original_edges": len(edges),
        "kept_nodes": len(kept_nodes),
        "kept_edges": len(kept_edges),
        "bridge_nodes_detected": len(bridge_ids),
        "bridge_nodes_kept": len(kept_bridge_ids),
    }


def _build_community_labels(nodes: list[dict]) -> dict[str, str]:
    """Строит человекочитаемые подписи сообществ по узлам: топ по влиянию + размер."""
    by_comm: dict[str, list[dict]] = defaultdict(list)
    for n in nodes or []:
        cid = str(int(n.get("community_id", 0) or 0))
        by_comm[cid].append(n)
    labels: dict[str, str] = {}
    for cid, members in by_comm.items():
        if not members:
            labels[cid] = f"Сообщество {cid}"
            continue
        top = max(members, key=lambda x: float(x.get("influence_score", 0.0) or 0.0))
        name = (top.get("label") or str(top.get("id", "")) or "").strip() or "?"
        size = len(members)
        if size == 1:
            labels[cid] = f"Участник {name}"
        else:
            labels[cid] = f"вокруг {name} ({size} чел.)"
    return labels


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


def _fetch_telegram_user_name(chat_id: int, user_id: int) -> str | None:
    """Запрос имени по Telegram API getChatMember. Возвращает first_name или username или None."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token or not chat_id or not user_id:
        return None
    try:
        url = f"https://api.telegram.org/bot{token}/getChatMember?chat_id={chat_id}&user_id={user_id}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
        if not data.get("ok"):
            return None
        user = (data.get("result") or {}).get("user") or {}
        first = (user.get("first_name") or "").strip()
        last = (user.get("last_name") or "").strip()
        username = (user.get("username") or "").strip()
        name = (first + " " + last).strip() or username or None
        return name if name else None
    except Exception:
        return None


def _enrich_display_names_for_nodes(names: dict, node_ids: set, chat_id: int | None = None) -> None:
    """Для узлов графа, у которых в names только id, подставить display_name из user_stats или Telegram API."""
    for uid in node_ids or []:
        if not uid:
            continue
        key = str(uid)
        if names.get(key, key) == key:
            try:
                u = user_stats.get_user(int(uid))
                dn = (u.get("display_name") or "").strip()
                if dn and dn != key:
                    names[key] = dn
                    continue
            except Exception:
                pass
            if chat_id:
                tg_name = _fetch_telegram_user_name(int(chat_id), int(uid))
                if tg_name:
                    names[key] = tg_name
                else:
                    names[key] = f"Участник (ID: {uid})"
            else:
                names[key] = f"Участник (ID: {uid})"


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
    _enrich_display_names_for_nodes(names, node_ids, chat_id=chat_id if isinstance(chat_id, int) else None)
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
                "community_label": "",  # заполним ниже из _build_community_labels
                "tier": tier,
            }
        )
    community_labels = _build_community_labels(nodes)
    for n in nodes:
        cid = str(int(n.get("community_id", 0)))
        n["community_label"] = community_labels.get(cid, f"Комьюнити {cid}")
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
    original_nodes = int(ds_meta.get("original_nodes", len(nodes)) or len(nodes))
    original_edges = int(ds_meta.get("original_edges", len(edges)) or len(edges))
    prefer_webgl = bool(
        bool(ds_meta.get("applied", False))
        or original_nodes >= WEBGL_HINT_NODE_THRESHOLD
        or original_edges >= WEBGL_HINT_EDGE_THRESHOLD
    )
    render_profile = "very_large" if prefer_webgl else ("medium" if (len(nodes) >= 120 or len(edges) >= 320) else "small")
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
            "community_labels": community_labels,
            "graph_engine": "networkx" if comm_algo.startswith("networkx") else "builtin",
            "downsampled": bool(ds_meta.get("applied", False)),
            "downsample_meta": ds_meta,
            "preferred_renderer": "webgl" if prefer_webgl else "standard",
            "render_profile": render_profile,
            "render_thresholds": {
                "webgl_nodes": WEBGL_HINT_NODE_THRESHOLD,
                "webgl_edges": WEBGL_HINT_EDGE_THRESHOLD,
            },
        },
    }


def _run_db_payload_sync(chat_id: int | None, period: str, ego_user: int | None, limit: int | None) -> dict:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        coro = _build_graph_payload_from_db(chat_id, period=period, ego_user=ego_user, limit=limit)
        try:
            return asyncio.run(coro)
        except Exception:
            # asyncio.run() may fail before coroutine execution (e.g. loop policy/runtime mismatch).
            # Explicit close avoids "coroutine was never awaited" runtime warnings.
            coro.close()
            raise
    raise RuntimeError("db graph payload cannot run via asyncio.run inside active event loop")


def build_graph_payload(chat_id: int | None, period: str = "all", ego_user: int | None = None, limit: int | None = None) -> dict:
    mode = get_storage_mode()
    if storage_db_reads_enabled(mode):
        try:
            db_payload = _run_db_payload_sync(chat_id, period=period, ego_user=ego_user, limit=limit)
            has_graph = bool((db_payload.get("nodes") or []) or (db_payload.get("edges") or []))
            if has_graph or storage_db_only_mode(mode):
                return db_payload
        except Exception:
            if storage_db_only_mode(mode):
                return {"nodes": [], "edges": [], "meta": {"chat_id": chat_id, "period": str(period or "all"), "source": "db_error"}}
    if not storage_json_fallback_enabled(mode) and storage_db_reads_enabled(mode):
        return {"nodes": [], "edges": [], "meta": {"chat_id": chat_id, "period": str(period or "all"), "source": "db_empty"}}

    rows = social_graph.get_connections(chat_id)
    if not rows:
        rows = _fallback_rows_from_user_stats(chat_id)
    names = user_stats.get_user_display_names() if hasattr(user_stats, "get_user_display_names") else {}
    payload = _build_payload_from_rows(chat_id=chat_id, rows=rows, names=names, period=period, ego_user=ego_user, limit=limit, rank_by_user={})
    payload.setdefault("meta", {})["source"] = "json"
    return payload


async def build_payload(chat_id: int | None, edge_repo, user_repo, period: int = 7, ego_user: int | None = None, limit: int | None = None) -> dict:
    if chat_id is None:
        edge_models = await edge_repo.get_all_chats()
        user_models = await user_repo.get_all_active()
    else:
        edge_models = await edge_repo.get_all(chat_id)
        user_models = await user_repo.get_all(chat_id)

    names = {}
    if hasattr(user_stats, "get_user_display_names"):
        try:
            names.update(user_stats.get_user_display_names() or {})
        except Exception:
            pass
    for u in user_models or []:
        uid = int(getattr(u, "id", 0) or 0)
        if uid:
            first = str(getattr(u, "first_name", "") or "").strip()
            username = str(getattr(u, "username", "") or "").strip()
            db_name = first or username or str(uid)
            if db_name and not str(db_name).lstrip("-").isdigit():
                names[str(uid)] = db_name
            else:
                names.setdefault(str(uid), db_name)
    rows = [
        {
            "user_a": int(getattr(e, "from_user", 0) or 0),
            "user_b": int(getattr(e, "to_user", 0) or 0),
            "message_count_total": float(getattr(e, "weight", 0.0) or 0.0),
            "message_count_7d": float(getattr(e, "period_7d", 0.0) or 0.0),
            "message_count_30d": float(getattr(e, "period_30d", 0.0) or 0.0),
            "message_count_a_to_b": int(getattr(e, "weight", 0.0) or 0.0),
            "message_count_b_to_a": int(getattr(e, "weight", 0.0) or 0.0),
            "tone": str(getattr(e, "tone", "neutral") or "neutral"),
            "topics": list(getattr(e, "topics", None) or []),
            "summary": str(getattr(e, "summary", "") or ""),
        }
        for e in edge_models or []
    ]
    period_key = "7d" if int(period) == 7 else ("30d" if int(period) == 30 else ("24h" if int(period) == 1 else "all"))
    payload = _build_payload_from_rows(
        chat_id=chat_id,
        rows=rows,
        names=names,
        period=period_key,
        ego_user=ego_user,
        limit=limit,
        rank_by_user={},
    )
    payload.setdefault("meta", {})["source"] = "db"
    return payload


async def _build_graph_payload_from_db(chat_id: int | None, period: str = "all", ego_user: int | None = None, limit: int | None = None) -> dict:
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
            None if chat_id is None else int(chat_id),
            edge_repo,
            user_repo,
            period=period_days,
            ego_user=ego_user,
            limit=limit,
        )
    return payload


async def get_connection_rows_from_db_async(chat_id: int | None) -> list[dict]:
    """Связи из БД в формате строк для дайджеста/анализа (с summary/tone/topics)."""
    from datetime import date, timedelta

    async with get_db() as session:
        edge_repo = EdgeRepository(session)
        if chat_id is None:
            edge_models = await edge_repo.get_all_chats()
        else:
            edge_models = await edge_repo.get_all(int(chat_id))

    today = date.today()
    yesterday_iso = (today - timedelta(days=1)).isoformat()

    rows = []
    for e in edge_models or []:
        cid = int(getattr(e, "chat_id", 0) or 0)
        ua = int(getattr(e, "from_user", 0) or 0)
        ub = int(getattr(e, "to_user", 0) or 0)
        if not ua or not ub:
            continue
        w = float(getattr(e, "weight", 0) or 0)
        p7 = float(getattr(e, "period_7d", 0) or 0)
        p30 = float(getattr(e, "period_30d", 0) or 0)
        tone = str(getattr(e, "tone", "neutral") or "neutral")
        topics = list(getattr(e, "topics", None) or [])
        summary = str(getattr(e, "summary", "") or "")
        summary_by_date = list(getattr(e, "summary_by_date", None) or [])
        last_upd = getattr(e, "last_updated", None)
        last_upd_str = last_upd.isoformat() if last_upd else ""

        c24 = sum(
            int(entry.get("message_count", 0) or 0)
            for entry in summary_by_date
            if str(entry.get("date", "")) >= yesterday_iso
        )

        rows.append({
            "chat_id": cid,
            "user_a": ua,
            "user_b": ub,
            "message_count": w,
            "message_count_total": w,
            "message_count_24h": c24,
            "message_count_7d": p7,
            "message_count_30d": p30,
            "message_count_a_to_b": int(w),
            "message_count_b_to_a": int(w),
            "summary": summary,
            "summary_by_date": summary_by_date,
            "last_updated": last_upd_str,
            "tone": tone,
            "topics": topics,
            "trend_delta": 0,
            "confidence": 0.0,
            "tone_trend": "stable",
            "connection_cooling": False,
            "alert_flags": [],
        })
    return rows


def get_connection_rows_from_db_sync(chat_id: int | None) -> list[dict]:
    """Синхронная обёртка для вызова из Flask/social_graph."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(get_connection_rows_from_db_async(chat_id))
    raise RuntimeError("get_connection_rows_from_db_sync cannot run inside active event loop")


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
