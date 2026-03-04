import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_user_stats() -> None:
    data = _load(ROOT / "user_stats.json")
    assert isinstance(data, dict)
    assert "users" in data and "chats" in data
    for _, u in (data.get("users") or {}).items():
        assert "stats" in u
        assert "messages_by_chat" in u
        assert "images_archive" in u
        assert "total_messages" in u["stats"]


def verify_qod_tracking() -> None:
    data = _load(ROOT / "question_of_day_tracking.json")
    assert "by_reply" in data
    assert "by_user_private" in data


def verify_social_graph() -> None:
    data = _load(ROOT / "social_graph.json")
    assert "dialogue_log" in data
    assert "processed_dates" in data
    assert "connections" in data


def main() -> None:
    verify_user_stats()
    verify_qod_tracking()
    verify_social_graph()
    print("Contracts OK")


if __name__ == "__main__":
    main()
