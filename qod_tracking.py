"""
Общий модуль трекинга «вопросов дня» для бота и админки.
Позволяет определить, является ли ответ пользователя ответом на вопрос дня.
"""

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

TRACKING_PATH = Path(__file__).resolve().parent / "question_of_day_tracking.json"


def load() -> dict:
    """Загружает трекинг вопросов дня."""
    if not TRACKING_PATH.exists():
        return {"by_reply": {}, "by_user_private": {}}
    try:
        return json.loads(TRACKING_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"by_reply": {}, "by_user_private": {}}


def save(data: dict) -> None:
    """Сохраняет трекинг вопросов дня."""
    try:
        TRACKING_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=0)
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=TRACKING_PATH.parent) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        tmp_path.replace(TRACKING_PATH)
    except Exception:
        pass


def add(chat_id: int, message_id: int, user_id: int, question: str) -> None:
    """Добавляет отправленный вопрос дня в трекинг."""
    data = load()
    key = f"{chat_id}_{message_id}"
    today = date.today().isoformat()
    data.setdefault("by_reply", {})[key] = {"user_id": user_id, "question": question[:200], "sent_at": today}
    if chat_id == user_id:
        data.setdefault("by_user_private", {})[str(user_id)] = {"message_id": message_id, "sent_at": today}
    # Очистка старых записей (старше 2 дней)
    cutoff = (date.today() - timedelta(days=2)).isoformat()
    for d in (data.get("by_reply", {}), data.get("by_user_private", {})):
        to_del = [k for k, v in d.items() if isinstance(v, dict) and (v.get("sent_at") or "") < cutoff]
        for k in to_del:
            del d[k]
    save(data)
