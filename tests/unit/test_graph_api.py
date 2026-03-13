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
