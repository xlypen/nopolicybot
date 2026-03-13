from services import graph_api


def test_build_graph_payload_empty():
    payload = graph_api._build_payload_from_rows(chat_id=1, rows=[], names={}, period="7d", ego_user=None, limit=None, rank_by_user={})
    assert payload["nodes"] == []
    assert payload["edges"] == []
    assert payload["meta"]["nodes_count"] == 0


def test_build_payload_limit_keeps_top_edges():
    rows = [
        {"user_a": 1, "user_b": 2, "message_count_total": 10, "message_count_7d": 10},
        {"user_a": 2, "user_b": 3, "message_count_total": 4, "message_count_7d": 4},
        {"user_a": 3, "user_b": 4, "message_count_total": 2, "message_count_7d": 2},
        {"user_a": 4, "user_b": 5, "message_count_total": 1, "message_count_7d": 1},
    ]
    payload = graph_api._build_payload_from_rows(chat_id=1, rows=rows, names={}, period="7d", ego_user=None, limit=2, rank_by_user={})
    assert len(payload["edges"]) >= 2
    weights = [e["weight_period"] for e in payload["edges"]]
    assert min(weights) >= 2


def test_downsampling_applies_for_large_graph():
    nodes = [{"id": i, "influence_score": float(i)} for i in range(500)]
    edges = [{"source": i, "target": i + 1, "weight": 1.0, "weight_period": 1.0} for i in range(400)]
    out_nodes, out_edges, meta = graph_api._downsample_large_graph(nodes, edges, max_nodes=100, max_edges=100)
    assert meta["applied"] is True
    assert len(out_nodes) <= 100
    assert len(out_edges) <= 100


def test_downsampling_preserves_bridge_endpoints():
    nodes = [{"id": i, "influence_score": float(i)} for i in range(1, 11)]
    edges = [
        {"source": 10, "target": 9, "weight": 4.0, "weight_period": 4.0, "bridge_score": 0.0, "community_id": 0},
        {"source": 9, "target": 8, "weight": 3.0, "weight_period": 3.0, "bridge_score": 0.0, "community_id": 0},
        {"source": 8, "target": 7, "weight": 2.0, "weight_period": 2.0, "bridge_score": 0.0, "community_id": 0},
        {"source": 1, "target": 2, "weight": 1.0, "weight_period": 1.0, "bridge_score": 1.0, "community_id": -1},
    ]
    out_nodes, out_edges, meta = graph_api._downsample_large_graph(nodes, edges, max_nodes=4, max_edges=4)
    out_ids = {int(n["id"]) for n in out_nodes}
    assert 1 in out_ids and 2 in out_ids
    assert any(int(e.get("source", 0)) in {1, 2} and int(e.get("target", 0)) in {1, 2} for e in out_edges)
    assert meta["bridge_nodes_detected"] >= 2
    assert meta["bridge_nodes_kept"] >= 2


def test_build_payload_contains_graph_engine_analytics_fields():
    rows = [
        {"user_a": 1, "user_b": 2, "message_count_total": 10, "message_count_7d": 8, "message_count_30d": 10},
        {"user_a": 2, "user_b": 3, "message_count_total": 7, "message_count_7d": 5, "message_count_30d": 7},
    ]
    payload = graph_api._build_payload_from_rows(chat_id=1, rows=rows, names={}, period="7d", ego_user=None, limit=None, rank_by_user={})
    assert payload["nodes"]
    assert payload["edges"]
    node = payload["nodes"][0]
    assert "centrality" in node
    assert "influence_score" in node
    meta = payload["meta"]
    assert meta["graph_engine"] in {"networkx", "builtin"}
    assert isinstance(meta["communities_algo"], str) and meta["communities_algo"]
    assert meta["preferred_renderer"] in {"standard", "webgl"}
    assert meta["render_profile"] in {"small", "medium", "very_large"}
    assert isinstance(meta["render_thresholds"], dict)
    assert int(meta["render_thresholds"]["webgl_nodes"]) > 0
    assert int(meta["render_thresholds"]["webgl_edges"]) > 0


def test_build_payload_marks_very_large_graph_for_webgl():
    rows = []
    for i in range(1, 900):
        rows.append(
            {
                "user_a": i,
                "user_b": i + 1,
                "message_count_total": 1,
                "message_count_7d": 1,
                "message_count_30d": 1,
            }
        )
    payload = graph_api._build_payload_from_rows(chat_id=1, rows=rows, names={}, period="7d", ego_user=None, limit=None, rank_by_user={})
    meta = payload["meta"]
    assert meta["preferred_renderer"] == "webgl"
    assert meta["render_profile"] == "very_large"
    assert bool(meta["downsampled"]) is True
