import json
import tempfile
import time
from pathlib import Path


PATH = Path(__file__).resolve().parent / "bot_explainability.json"
MAX_EVENTS = 600


def _load() -> dict:
    if not PATH.exists():
        return {"events": []}
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            return data
    except Exception:
        pass
    return {"events": []}


def _save(data: dict) -> None:
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=PATH.parent) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        tmp_path.replace(PATH)
    except Exception:
        pass


def append_event(kind: str, decision: str, chat_id: int | None = None, user_id: int | None = None, detail: str = "") -> None:
    data = _load()
    events = data.setdefault("events", [])
    events.append({
        "ts": time.time(),
        "kind": (kind or "")[:40],
        "decision": (decision or "")[:200],
        "chat_id": int(chat_id) if chat_id is not None else None,
        "user_id": int(user_id) if user_id is not None else None,
        "detail": (detail or "")[:400],
    })
    data["events"] = events[-MAX_EVENTS:]
    _save(data)


def get_recent(limit: int = 80, chat_id: int | None = None, user_id: int | None = None) -> list[dict]:
    data = _load()
    rows = list(reversed(data.get("events") or []))
    out = []
    for e in rows:
        if chat_id is not None and int(e.get("chat_id") or 0) != int(chat_id):
            continue
        if user_id is not None and int(e.get("user_id") or 0) != int(user_id):
            continue
        out.append(e)
        if len(out) >= max(1, int(limit)):
            break
    return out
