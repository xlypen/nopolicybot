"""Тесты сходства участников на /me."""

import asyncio

import pytest

from services import participant_similarity as ps


def test_edge_weight_map():
    conns = [
        {"user_a": 1, "user_b": 2, "chat_id": 10, "message_count_7d": 3},
        {"user_a": 2, "user_b": 1, "chat_id": 10, "message_count_7d": 7},
    ]
    m = ps._edge_weight_map(conns, 1)
    assert m[(10, 2)] == 7


def test_pick_extremes_two_peers():
    cands = [
        {"chat_id": 1, "peer_id": 10, "score": 0.9, "hint": "a", "source": "t", "peer_name": "A"},
        {"chat_id": 1, "peer_id": 11, "score": 0.3, "hint": "b", "source": "t", "peer_name": "B"},
    ]
    wmap = {(1, 10): 5, (1, 11): 20}
    most, least = ps._pick_extremes(cands, wmap)
    assert most["peer_id"] == 10
    assert least["peer_id"] == 11


def test_pick_extremes_single_candidate():
    cands = [{"chat_id": 1, "peer_id": 10, "score": 0.5, "hint": "a", "source": "t", "peer_name": "A"}]
    most, least = ps._pick_extremes(cands, {(1, 10): 1})
    assert most["peer_id"] == 10
    assert least is None


def test_build_falls_back_to_heuristic(monkeypatch):
    def _fake_run(coro):
        if asyncio.iscoroutine(coro):
            coro.close()
        return []

    monkeypatch.setattr(asyncio, "run", _fake_run)

    def fake_get_user(uid, dn=""):
        if uid == 20:
            return {"rank": "loyal"}
        if uid == 21:
            return {"rank": "loyal"}
        if uid == 22:
            return {"rank": "opposition"}
        return {"rank": "unknown"}

    monkeypatch.setattr("user_stats.get_user", fake_get_user)

    conns = [
        {"user_a": 1, "user_b": 21, "chat_id": 100, "message_count_7d": 10},
        {"user_a": 1, "user_b": 22, "chat_id": 100, "message_count_7d": 15},
    ]
    names = {"21": "U21", "22": "U22"}
    out = ps.build_participant_similarity_peers(1, [100], conns, names, "loyal")
    assert out["ok"] is True
    assert out["source"] == "heuristic"
    assert out["most"]["peer_id"] == 21
    assert out["least"]["peer_id"] == 22
