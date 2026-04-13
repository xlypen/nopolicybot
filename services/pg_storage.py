"""
PgStorage: тот же контракт, что SqliteStorage, но через PostgreSQL (sync_session_scope).
Используется при DATABASE_URL на Postgres — единая БД вместо data/bot.db.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from sqlalchemy import text

from db.sync_engine import sync_session_scope
from services.storage_cutover import storage_db_writes_enabled

logger = logging.getLogger(__name__)

_GRAPH_META_ID = 2


class PgStorage:
    """Хранилище на Postgres (таблицы из db/models.py)."""

    @staticmethod
    def ensure_tables() -> None:
        """Создать отсутствующие таблицы (в т.ч. dialogue_messages после обновления)."""
        from db.models import Base
        from db.sync_engine import get_sync_engine

        Base.metadata.create_all(bind=get_sync_engine())

    def close(self) -> None:
        """Совместимость с SqliteStorage; пул не закрываем."""

    def get_user_profile(self, user_id: int) -> dict | None:
        with sync_session_scope() as session:
            row = session.execute(
                text("SELECT profile_json FROM user_profiles WHERE user_id = :uid"),
                {"uid": int(user_id)},
            ).fetchone()
        if not row:
            return None
        raw = row[0]
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return raw or {}

    def set_user_profile(self, user_id: int, profile: dict) -> None:
        if not storage_db_writes_enabled():
            return
        payload = json.dumps(profile, ensure_ascii=False)
        with sync_session_scope() as session:
            session.execute(
                text(
                    """
                    INSERT INTO user_profiles (user_id, profile_json)
                    VALUES (:uid, CAST(:payload AS jsonb))
                    ON CONFLICT (user_id) DO UPDATE SET profile_json = EXCLUDED.profile_json
                    """
                ),
                {"uid": int(user_id), "payload": payload},
            )

    def get_user_messages(
        self, user_id: int, chat_id: int | None = None, limit: int = 1000
    ) -> list[dict]:
        with sync_session_scope() as session:
            if chat_id is not None:
                rows = session.execute(
                    text(
                        """
                        SELECT text, sent_at, chat_id FROM messages
                        WHERE user_id = :uid AND chat_id = :cid
                          AND text IS NOT NULL AND text != ''
                        ORDER BY sent_at ASC LIMIT :lim
                        """
                    ),
                    {"uid": int(user_id), "cid": int(chat_id), "lim": int(limit)},
                ).fetchall()
            else:
                rows = session.execute(
                    text(
                        """
                        SELECT text, sent_at, chat_id FROM messages
                        WHERE user_id = :uid AND text IS NOT NULL AND text != ''
                        ORDER BY sent_at ASC LIMIT :lim
                        """
                    ),
                    {"uid": int(user_id), "lim": int(limit)},
                ).fetchall()
            result = []
            for row in rows:
                t = (row[0] or "").strip()
                sent_at = row[1]
                cid = row[2] if len(row) > 2 else None
                date_str = str(sent_at)[:10] if sent_at else ""
                msg = {"text": t, "date": date_str}
                if cid is not None and chat_id is None:
                    msg["chat_id"] = int(cid)
                result.append(msg)
            if result:
                return result
            if chat_id is not None:
                rows = session.execute(
                    text(
                        """
                        SELECT text, date FROM user_message_archive
                        WHERE user_id = :uid AND chat_id = :cid
                        ORDER BY date, id LIMIT :lim
                        """
                    ),
                    {"uid": int(user_id), "cid": int(chat_id), "lim": int(limit)},
                ).fetchall()
                return [{"text": r[0], "date": r[1]} for r in rows]
            rows = session.execute(
                text(
                    """
                    SELECT text, date, chat_id FROM user_message_archive
                    WHERE user_id = :uid ORDER BY date, id LIMIT :lim
                    """
                ),
                {"uid": int(user_id), "lim": int(limit)},
            ).fetchall()
            return [{"text": r[0], "date": r[1], "chat_id": r[2]} for r in rows]

    def get_display_names(self) -> dict[str, str]:
        with sync_session_scope() as session:
            rows = session.execute(
                text("SELECT id, first_name, username, last_name FROM users")
            ).fetchall()
        result: dict[str, str] = {}
        for uid, first, username, last in rows:
            name = (first or username or last or "").strip() or str(uid)
            result[str(uid)] = name
        return result

    def get_users_in_chat(self, chat_id: int) -> list[int]:
        with sync_session_scope() as session:
            rows = session.execute(
                text(
                    """
                    SELECT DISTINCT user_id FROM messages
                    WHERE chat_id = :cid AND user_id IS NOT NULL
                    """
                ),
                {"cid": int(chat_id)},
            ).fetchall()
        return [r[0] for r in rows]

    def increment_warnings(self, user_id: int) -> None:
        if not storage_db_writes_enabled():
            return
        with sync_session_scope() as session:
            session.execute(
                text(
                    """
                    UPDATE users SET warnings_received = COALESCE(warnings_received, 0) + 1
                    WHERE id = :uid
                    """
                ),
                {"uid": int(user_id)},
            )

    def append_message(
        self, user_id: int, chat_id: int, text_val: str, date_str: str, *, dedupe: bool = True
    ) -> bool:
        if not storage_db_writes_enabled():
            return False
        t = text_val[:500]
        with sync_session_scope() as session:
            if dedupe:
                exists = session.execute(
                    text(
                        """
                        SELECT 1 FROM user_message_archive
                        WHERE user_id = :uid AND chat_id = :cid AND text = :txt AND date = :d
                        """
                    ),
                    {"uid": int(user_id), "cid": int(chat_id), "txt": t, "d": date_str},
                ).fetchone()
                if exists:
                    return False
            session.execute(
                text(
                    """
                    INSERT INTO user_message_archive (user_id, chat_id, text, date)
                    VALUES (:uid, :cid, :txt, :d)
                    """
                ),
                {"uid": int(user_id), "cid": int(chat_id), "txt": t, "d": date_str},
            )
        return True

    def get_chat(self, chat_id: int) -> dict | None:
        with sync_session_scope() as session:
            row = session.execute(
                text(
                    "SELECT chat_id, title, last_seen FROM storage_chats WHERE chat_id = :cid"
                ),
                {"cid": int(chat_id)},
            ).fetchone()
        if not row:
            return None
        return {"chat_id": row[0], "title": row[1] or "", "last_seen": row[2] or ""}

    def upsert_chat(self, chat_id: int, title: str) -> None:
        if not storage_db_writes_enabled():
            return
        today = date.today().isoformat()
        with sync_session_scope() as session:
            session.execute(
                text(
                    """
                    INSERT INTO storage_chats (chat_id, title, last_seen)
                    VALUES (:cid, :title, :seen)
                    ON CONFLICT (chat_id) DO UPDATE SET
                      title = EXCLUDED.title, last_seen = EXCLUDED.last_seen
                    """
                ),
                {"cid": int(chat_id), "title": title or "", "seen": today},
            )

    def list_storage_chats(self) -> list[dict]:
        with sync_session_scope() as session:
            rows = session.execute(
                text("SELECT chat_id, title, last_seen FROM storage_chats ORDER BY chat_id")
            ).fetchall()
        return [
            {"chat_id": int(r[0]), "title": r[1] or "", "last_seen": r[2] or ""}
            for r in rows
        ]

    def iter_user_profiles(self) -> list[tuple[int, dict]]:
        with sync_session_scope() as session:
            rows = session.execute(
                text("SELECT user_id, profile_json FROM user_profiles ORDER BY user_id")
            ).fetchall()
        out: list[tuple[int, dict]] = []
        for uid, raw in rows:
            try:
                if isinstance(raw, dict):
                    d = raw
                elif isinstance(raw, str):
                    d = json.loads(raw)
                else:
                    d = raw or {}
                if isinstance(d, dict):
                    out.append((int(uid), d))
            except Exception:
                continue
        return out

    def delete_user_message_archive(self, user_id: int, chat_id: int | None = None) -> int:
        if not storage_db_writes_enabled():
            return 0
        with sync_session_scope() as session:
            if chat_id is not None:
                result = session.execute(
                    text(
                        "DELETE FROM user_message_archive WHERE user_id = :uid AND chat_id = :cid"
                    ),
                    {"uid": int(user_id), "cid": int(chat_id)},
                )
            else:
                result = session.execute(
                    text("DELETE FROM user_message_archive WHERE user_id = :uid"),
                    {"uid": int(user_id)},
                )
            return int(result.rowcount or 0)

    def get_graph_meta(self) -> dict:
        with sync_session_scope() as session:
            row = session.execute(
                text("SELECT data_json FROM storage_settings WHERE id = :sid"),
                {"sid": _GRAPH_META_ID},
            ).fetchone()
        if not row or not row[0]:
            return {}
        raw = row[0]
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            try:
                d = json.loads(raw)
                return dict(d) if isinstance(d, dict) else {}
            except Exception:
                return {}
        return {}

    def replace_graph_meta(self, data: dict) -> None:
        if not storage_db_writes_enabled():
            return
        payload = json.dumps(data if isinstance(data, dict) else {}, ensure_ascii=False)
        with sync_session_scope() as session:
            session.execute(
                text(
                    """
                    INSERT INTO storage_settings (id, data_json)
                    VALUES (:sid, CAST(:payload AS jsonb))
                    ON CONFLICT (id) DO UPDATE SET data_json = EXCLUDED.data_json
                    """
                ),
                {"sid": _GRAPH_META_ID, "payload": payload},
            )

    def append_dialogue_message(
        self,
        chat_id: int,
        date_str: str,
        sender_id: int,
        sender_name: str,
        text_val: str,
        reply_to_user_id: int | None = None,
    ) -> None:
        if not storage_db_writes_enabled():
            return
        with sync_session_scope() as session:
            session.execute(
                text(
                    """
                    INSERT INTO dialogue_messages
                      (chat_id, date, sender_id, sender_name, text, reply_to_user_id)
                    VALUES (:cid, :d, :sid, :sname, :txt, :rid)
                    """
                ),
                {
                    "cid": int(chat_id),
                    "d": date_str,
                    "sid": int(sender_id),
                    "sname": (sender_name or "")[:50],
                    "txt": text_val[:300],
                    "rid": int(reply_to_user_id) if reply_to_user_id else None,
                },
            )

    def get_dialogue_messages(self, chat_id: int, date_str: str) -> list[dict]:
        with sync_session_scope() as session:
            rows = session.execute(
                text(
                    """
                    SELECT sender_id, sender_name, text, reply_to_user_id
                    FROM dialogue_messages
                    WHERE chat_id = :cid AND date = :d
                    ORDER BY id
                    """
                ),
                {"cid": int(chat_id), "d": date_str},
            ).fetchall()
        return [
            {
                "sender_id": r[0],
                "sender_name": r[1],
                "text": r[2],
                "reply_to_user_id": r[3],
            }
            for r in rows
        ]

    def get_distinct_dialogue_dates(self, chat_id: int, before_date: str) -> list[str]:
        with sync_session_scope() as session:
            rows = session.execute(
                text(
                    """
                    SELECT DISTINCT date FROM dialogue_messages
                    WHERE chat_id = :cid AND date < :before
                    ORDER BY date
                    """
                ),
                {"cid": int(chat_id), "before": before_date},
            ).fetchall()
        return [r[0] for r in rows]

    def get_all_dialogue_chat_ids(self) -> list[int]:
        with sync_session_scope() as session:
            rows = session.execute(
                text("SELECT DISTINCT chat_id FROM dialogue_messages")
            ).fetchall()
        return [r[0] for r in rows]

    def delete_dialogue_before(self, chat_id: int, cutoff_date: str) -> int:
        if not storage_db_writes_enabled():
            return 0
        with sync_session_scope() as session:
            result = session.execute(
                text(
                    "DELETE FROM dialogue_messages WHERE chat_id = :cid AND date < :cut"
                ),
                {"cid": int(chat_id), "cut": cutoff_date},
            )
            return int(result.rowcount or 0)

    @staticmethod
    def _pair_to_users(pair_key: str) -> tuple[int, int]:
        parts = pair_key.split("|")
        if len(parts) != 2:
            return 0, 0
        a, b = int(parts[0]), int(parts[1])
        return min(a, b), max(a, b)

    def get_connection(self, chat_id: int, pair_key: str) -> dict | None:
        ua, ub = self._pair_to_users(pair_key)
        if not ua and not ub:
            return None
        with sync_session_scope() as session:
            row = session.execute(
                text(
                    """
                    SELECT summary, summary_by_date, weight FROM edges
                    WHERE chat_id = :cid AND from_user = :ua AND to_user = :ub
                    """
                ),
                {"cid": int(chat_id), "ua": ua, "ub": ub},
            ).fetchone()
        if not row:
            return None
        sbd_raw = row[1]
        if isinstance(sbd_raw, list):
            summary_by_date = sbd_raw
        elif isinstance(sbd_raw, str):
            try:
                summary_by_date = json.loads(sbd_raw)
            except Exception:
                summary_by_date = []
        else:
            summary_by_date = sbd_raw or []
        w = row[2] if len(row) > 2 else 0
        return {
            "summary": row[0] or "",
            "summary_by_date": summary_by_date,
            "user_a": ua,
            "user_b": ub,
            "message_count": int(w or 0),
        }

    def upsert_connection(self, chat_id: int, pair_key: str, data: dict) -> None:
        if not storage_db_writes_enabled():
            return
        ua, ub = self._pair_to_users(pair_key)
        if not ua and not ub:
            return
        summary = (data.get("summary") or "")[:6000]
        summary_by_date = data.get("summary_by_date") or []
        payload = json.dumps(summary_by_date, ensure_ascii=False)
        weight = int(data.get("message_count", 0) or 0)
        if weight < 1:
            weight = 1
        with sync_session_scope() as session:
            session.execute(
                text(
                    """
                    INSERT INTO edges (chat_id, from_user, to_user, weight, summary, summary_by_date)
                    VALUES (:cid, :ua, :ub, :w, :sm, CAST(:sbd AS jsonb))
                    ON CONFLICT (chat_id, from_user, to_user) DO UPDATE SET
                      summary = EXCLUDED.summary,
                      summary_by_date = EXCLUDED.summary_by_date,
                      weight = EXCLUDED.weight
                    """
                ),
                {
                    "cid": int(chat_id),
                    "ua": ua,
                    "ub": ub,
                    "w": weight,
                    "sm": summary,
                    "sbd": payload,
                },
            )

    def get_all_connections(self, chat_id: int | None) -> list[dict]:
        with sync_session_scope() as session:
            if chat_id is not None:
                rows = session.execute(
                    text(
                        """
                        SELECT chat_id, from_user, to_user, summary, summary_by_date, weight
                        FROM edges WHERE chat_id = :cid
                        """
                    ),
                    {"cid": int(chat_id)},
                ).fetchall()
            else:
                rows = session.execute(
                    text(
                        """
                        SELECT chat_id, from_user, to_user, summary, summary_by_date, weight
                        FROM edges
                        """
                    )
                ).fetchall()
        out = []
        for r in rows:
            sbd_raw = r[4]
            if isinstance(sbd_raw, list):
                sbd = sbd_raw
            elif isinstance(sbd_raw, str):
                try:
                    sbd = json.loads(sbd_raw)
                except Exception:
                    sbd = []
            else:
                sbd = sbd_raw or []
            w = int(r[5] or 0) if len(r) > 5 else 0
            out.append(
                {
                    "chat_id": r[0],
                    "pair_key": f"{min(r[1], r[2])}|{max(r[1], r[2])}",
                    "summary": r[3] or "",
                    "summary_by_date": sbd,
                    "message_count": w,
                }
            )
        return out

    def get_last_processed_date(self) -> str | None:
        with sync_session_scope() as session:
            row = session.execute(
                text("SELECT data_json FROM storage_settings WHERE id = :sid"),
                {"sid": _GRAPH_META_ID},
            ).fetchone()
        if not row:
            return None
        raw = row[0]
        if isinstance(raw, dict):
            data = raw
        elif isinstance(raw, str):
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
        else:
            data = raw or {}
        return data.get("last_processed_date") or None

    def set_last_processed_date(self, date_str: str) -> None:
        if not storage_db_writes_enabled():
            return
        with sync_session_scope() as session:
            row = session.execute(
                text("SELECT data_json FROM storage_settings WHERE id = :sid"),
                {"sid": _GRAPH_META_ID},
            ).fetchone()
            data: dict = {}
            if row and row[0]:
                raw = row[0]
                if isinstance(raw, dict):
                    data = dict(raw)
                elif isinstance(raw, str):
                    try:
                        data = json.loads(raw) if raw else {}
                    except Exception:
                        data = {}
                else:
                    data = raw or {}
            data["last_processed_date"] = date_str
            payload = json.dumps(data, ensure_ascii=False)
            session.execute(
                text(
                    """
                    INSERT INTO storage_settings (id, data_json)
                    VALUES (:sid, CAST(:payload AS jsonb))
                    ON CONFLICT (id) DO UPDATE SET data_json = EXCLUDED.data_json
                    """
                ),
                {"sid": _GRAPH_META_ID, "payload": payload},
            )

    def get_processed_dates_for_chat(self, chat_id: int) -> set[str]:
        try:
            with sync_session_scope() as session:
                rows = session.execute(
                    text(
                        "SELECT processed_date FROM processed_dates WHERE chat_id = :cid"
                    ),
                    {"cid": int(chat_id)},
                ).fetchall()
            return {r[0] for r in rows}
        except Exception:
            return set()

    def set_processed_date(self, chat_id: int, date_str: str) -> None:
        if not storage_db_writes_enabled():
            return
        try:
            with sync_session_scope() as session:
                session.execute(
                    text(
                        """
                        INSERT INTO processed_dates (chat_id, processed_date)
                        VALUES (:cid, :d)
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {"cid": int(chat_id), "d": date_str},
                )
        except Exception as e:
            logger.debug("set_processed_date: %s", e)

    def get_global_settings(self) -> dict:
        with sync_session_scope() as session:
            row = session.execute(
                text("SELECT data_json FROM storage_settings WHERE id = 1")
            ).fetchone()
        if not row:
            return {}
        raw = row[0]
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return raw or {}

    def set_global_settings(self, data: dict) -> None:
        if not storage_db_writes_enabled():
            return
        payload = json.dumps(data, ensure_ascii=False)
        with sync_session_scope() as session:
            session.execute(
                text(
                    """
                    INSERT INTO storage_settings (id, data_json)
                    VALUES (1, CAST(:payload AS jsonb))
                    ON CONFLICT (id) DO UPDATE SET data_json = EXCLUDED.data_json
                    """
                ),
                {"payload": payload},
            )
