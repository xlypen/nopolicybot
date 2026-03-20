"""
SqliteStorage: IStorage implementation using data/bot.db (sync sqlite3).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from services.storage_cutover import storage_db_writes_enabled

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bot.db"
_GRAPH_META_ID = 2  # storage_settings.id=2 for graph metadata


def _get_conn():
    import sqlite3
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(_DB_PATH))


def _ensure_tables(conn) -> None:
    """Create storage tables if missing (user_profiles, storage_chats, etc.)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            profile_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS storage_chats (
            chat_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '',
            last_seen TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS user_message_archive (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            date TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_uma_user_chat ON user_message_archive(user_id, chat_id);
        CREATE INDEX IF NOT EXISTS idx_uma_user_chat_date ON user_message_archive(user_id, chat_id, date);
        CREATE TABLE IF NOT EXISTS dialogue_log (
            chat_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (chat_id, date)
        );
        CREATE TABLE IF NOT EXISTS dialogue_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            sender_id INTEGER NOT NULL,
            sender_name TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL,
            reply_to_user_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_dlg_chat_date ON dialogue_messages(chat_id, date);
        CREATE INDEX IF NOT EXISTS idx_dlg_chat_sender ON dialogue_messages(chat_id, sender_id);
        CREATE TABLE IF NOT EXISTS storage_settings (
            id INTEGER PRIMARY KEY,
            data_json TEXT NOT NULL DEFAULT '{}'
        );
    """)


class SqliteStorage:
    """IStorage implementation using data/bot.db."""

    def __init__(self, db_path: Path | None = None):
        self._path = db_path or _DB_PATH
        self._local = threading.local()

    def _conn(self):
        import sqlite3
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            _ensure_tables(conn)
            conn.commit()
            self._local.conn = conn
        return self._local.conn

    def close(self) -> None:
        """Закрыть соединение текущего потока."""
        conn = getattr(self._local, "conn", None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def get_user_profile(self, user_id: int) -> dict | None:
        conn = self._conn()
        row = conn.execute(
            "SELECT profile_json FROM user_profiles WHERE user_id = ?",
            (int(user_id),),
        ).fetchone()
        if not row:
            return None
        raw = row[0]
        return json.loads(raw) if isinstance(raw, str) else (raw or {})

    def set_user_profile(self, user_id: int, profile: dict) -> None:
        if not storage_db_writes_enabled():
            return
        conn = self._conn()
        payload = json.dumps(profile, ensure_ascii=False)
        conn.execute(
            "INSERT INTO user_profiles (user_id, profile_json) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET profile_json = excluded.profile_json",
            (int(user_id), payload),
        )
        conn.commit()

    def get_user_messages(
        self, user_id: int, chat_id: int | None = None, limit: int = 1000
    ) -> list[dict]:
        conn = self._conn()
        try:
            if chat_id is not None:
                rows = conn.execute(
                    """SELECT text, sent_at, chat_id FROM messages
                       WHERE user_id = ? AND chat_id = ?
                       AND text IS NOT NULL AND text != ''
                       ORDER BY sent_at ASC LIMIT ?""",
                    (int(user_id), int(chat_id), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT text, sent_at, chat_id FROM messages
                       WHERE user_id = ? AND text IS NOT NULL AND text != ''
                       ORDER BY sent_at ASC LIMIT ?""",
                    (int(user_id), limit),
                ).fetchall()
            result = []
            for row in rows:
                text = (row[0] or "").strip()
                sent_at = row[1]
                cid = row[2] if len(row) > 2 else None
                date_str = str(sent_at)[:10] if sent_at else ""
                msg = {"text": text, "date": date_str}
                if cid is not None and chat_id is None:
                    msg["chat_id"] = int(cid)
                result.append(msg)
            if result:
                return result
            if chat_id is not None:
                rows = conn.execute(
                    "SELECT text, date FROM user_message_archive "
                    "WHERE user_id = ? AND chat_id = ? ORDER BY date, id LIMIT ?",
                    (int(user_id), int(chat_id), limit),
                ).fetchall()
                return [{"text": r[0], "date": r[1]} for r in rows]
            rows = conn.execute(
                "SELECT text, date, chat_id FROM user_message_archive "
                "WHERE user_id = ? ORDER BY date, id LIMIT ?",
                (int(user_id), limit),
            ).fetchall()
            return [{"text": r[0], "date": r[1], "chat_id": r[2]} for r in rows]
        finally:
            pass

    def get_display_names(self) -> dict[str, str]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, first_name, username, last_name FROM users"
        ).fetchall()
        result = {}
        for uid, first, username, last in rows:
            name = (first or username or last or "").strip() or str(uid)
            result[str(uid)] = name
        return result

    def get_users_in_chat(self, chat_id: int) -> list[int]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM messages WHERE chat_id = ? AND user_id IS NOT NULL",
                (int(chat_id),),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            pass

    def increment_warnings(self, user_id: int) -> None:
        if not storage_db_writes_enabled():
            return
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE users SET warnings_received = COALESCE(warnings_received, 0) + 1 WHERE id = ?",
                (int(user_id),),
            )
            conn.commit()
        finally:
            pass

    def append_message(
        self, user_id: int, chat_id: int, text: str, date: str, *, dedupe: bool = True
    ) -> bool:
        if not storage_db_writes_enabled():
            return False
        conn = self._conn()
        try:
            if dedupe:
                exists = conn.execute(
                    "SELECT 1 FROM user_message_archive "
                    "WHERE user_id = ? AND chat_id = ? AND text = ? AND date = ?",
                    (int(user_id), int(chat_id), text[:500], date),
                ).fetchone()
                if exists:
                    return False
            conn.execute(
                "INSERT INTO user_message_archive (user_id, chat_id, text, date) VALUES (?, ?, ?, ?)",
                (int(user_id), int(chat_id), text[:500], date),
            )
            conn.commit()
            return True
        finally:
            pass

    def get_chat(self, chat_id: int) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT chat_id, title, last_seen FROM storage_chats WHERE chat_id = ?",
                (int(chat_id),),
            ).fetchone()
            if not row:
                return None
            return {"chat_id": row[0], "title": row[1] or "", "last_seen": row[2] or ""}
        finally:
            pass

    def upsert_chat(self, chat_id: int, title: str) -> None:
        if not storage_db_writes_enabled():
            return
        conn = self._conn()
        try:
            today = __import__("datetime").date.today().isoformat()
            conn.execute(
                "INSERT INTO storage_chats (chat_id, title, last_seen) VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title, last_seen = excluded.last_seen",
                (int(chat_id), title or "", today),
            )
            conn.commit()
        finally:
            pass

    def list_storage_chats(self) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT chat_id, title, last_seen FROM storage_chats ORDER BY chat_id"
            ).fetchall()
            return [
                {"chat_id": int(r[0]), "title": r[1] or "", "last_seen": r[2] or ""}
                for r in rows
            ]
        finally:
            pass

    def iter_user_profiles(self) -> list[tuple[int, dict]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT user_id, profile_json FROM user_profiles ORDER BY user_id"
            ).fetchall()
            out: list[tuple[int, dict]] = []
            for uid, raw in rows:
                try:
                    d = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    if isinstance(d, dict):
                        out.append((int(uid), d))
                except Exception:
                    continue
            return out
        finally:
            pass

    def delete_user_message_archive(self, user_id: int, chat_id: int | None = None) -> int:
        if not storage_db_writes_enabled():
            return 0
        conn = self._conn()
        try:
            if chat_id is not None:
                cur = conn.execute(
                    "DELETE FROM user_message_archive WHERE user_id = ? AND chat_id = ?",
                    (int(user_id), int(chat_id)),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM user_message_archive WHERE user_id = ?",
                    (int(user_id),),
                )
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            pass

    def get_graph_meta(self) -> dict:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT data_json FROM storage_settings WHERE id = ?",
                (_GRAPH_META_ID,),
            ).fetchone()
            if not row or not row[0]:
                return {}
            raw = row[0]
            d = json.loads(raw) if isinstance(raw, str) else (raw or {})
            return dict(d) if isinstance(d, dict) else {}
        finally:
            pass

    def replace_graph_meta(self, data: dict) -> None:
        if not storage_db_writes_enabled():
            return
        conn = self._conn()
        try:
            payload = json.dumps(data if isinstance(data, dict) else {}, ensure_ascii=False)
            conn.execute(
                "INSERT INTO storage_settings (id, data_json) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data_json = excluded.data_json",
                (_GRAPH_META_ID, payload),
            )
            conn.commit()
        finally:
            pass

    def append_dialogue_message(
        self,
        chat_id: int,
        date: str,
        sender_id: int,
        sender_name: str,
        text: str,
        reply_to_user_id: int | None = None,
    ) -> None:
        if not storage_db_writes_enabled():
            return
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO dialogue_messages
                   (chat_id, date, sender_id, sender_name, text, reply_to_user_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    int(chat_id),
                    date,
                    int(sender_id),
                    (sender_name or "")[:50],
                    text[:300],
                    int(reply_to_user_id) if reply_to_user_id else None,
                ),
            )
            conn.commit()
        finally:
            pass

    def get_dialogue_messages(self, chat_id: int, date: str) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT sender_id, sender_name, text, reply_to_user_id
                   FROM dialogue_messages
                   WHERE chat_id = ? AND date = ?
                   ORDER BY id""",
                (int(chat_id), date),
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
        finally:
            pass

    def get_distinct_dialogue_dates(self, chat_id: int, before_date: str) -> list[str]:
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT DISTINCT date FROM dialogue_messages
                   WHERE chat_id = ? AND date < ?
                   ORDER BY date""",
                (int(chat_id), before_date),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            pass

    def get_all_dialogue_chat_ids(self) -> list[int]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT chat_id FROM dialogue_messages"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            pass

    def delete_dialogue_before(self, chat_id: int, cutoff_date: str) -> int:
        conn = self._conn()
        try:
            cur = conn.execute(
                "DELETE FROM dialogue_messages WHERE chat_id = ? AND date < ?",
                (int(chat_id), cutoff_date),
            )
            conn.commit()
            return cur.rowcount
        finally:
            pass

    def _pair_to_users(self, pair_key: str) -> tuple[int, int]:
        parts = pair_key.split("|")
        if len(parts) != 2:
            return 0, 0
        a, b = int(parts[0]), int(parts[1])
        return min(a, b), max(a, b)

    def get_connection(self, chat_id: int, pair_key: str) -> dict | None:
        ua, ub = self._pair_to_users(pair_key)
        if not ua and not ub:
            return None
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT summary, summary_by_date, weight FROM edges "
                "WHERE chat_id = ? AND from_user = ? AND to_user = ?",
                (int(chat_id), ua, ub),
            ).fetchone()
            if not row:
                return None
            summary_by_date = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or [])
            w = row[2] if len(row) > 2 else 0
            return {
                "summary": row[0] or "",
                "summary_by_date": summary_by_date,
                "user_a": ua,
                "user_b": ub,
                "message_count": int(w or 0),
            }
        finally:
            pass

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
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO edges (chat_id, from_user, to_user, weight, summary, summary_by_date) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(chat_id, from_user, to_user) DO UPDATE SET "
                "summary = excluded.summary, summary_by_date = excluded.summary_by_date, "
                "weight = excluded.weight",
                (int(chat_id), ua, ub, weight, summary, payload),
            )
            conn.commit()
        except Exception as e:
            # edges may have different schema (unique constraint name)
            try:
                conn.execute(
                    "UPDATE edges SET summary = ?, summary_by_date = ?, weight = ? "
                    "WHERE chat_id = ? AND from_user = ? AND to_user = ?",
                    (summary, payload, weight, int(chat_id), ua, ub),
                )
                if conn.total_changes == 0:
                    conn.execute(
                        "INSERT INTO edges (chat_id, from_user, to_user, weight, summary, summary_by_date) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (int(chat_id), ua, ub, weight, summary, payload),
                    )
                conn.commit()
            except Exception as e2:
                logger.warning("upsert_connection: %s", e2)
    def get_all_connections(self, chat_id: int | None) -> list[dict]:
        conn = self._conn()
        try:
            if chat_id is not None:
                rows = conn.execute(
                    "SELECT chat_id, from_user, to_user, summary, summary_by_date, weight "
                    "FROM edges WHERE chat_id = ?",
                    (int(chat_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT chat_id, from_user, to_user, summary, summary_by_date, weight FROM edges"
                ).fetchall()
            out = []
            for r in rows:
                sbd = json.loads(r[4]) if isinstance(r[4], str) else (r[4] or [])
                w = int(r[5] or 0) if len(r) > 5 else 0
                out.append({
                    "chat_id": r[0],
                    "pair_key": f"{min(r[1], r[2])}|{max(r[1], r[2])}",
                    "summary": r[3] or "",
                    "summary_by_date": sbd,
                    "message_count": w,
                })
            return out
        finally:
            pass

    def get_last_processed_date(self) -> str | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT data_json FROM storage_settings WHERE id = ?",
                (_GRAPH_META_ID,),
            ).fetchone()
            if not row:
                return None
            data = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
            return data.get("last_processed_date") or None
        finally:
            pass

    def set_last_processed_date(self, date: str) -> None:
        if not storage_db_writes_enabled():
            return
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT data_json FROM storage_settings WHERE id = ?",
                (_GRAPH_META_ID,),
            ).fetchone()
            data = {}
            if row and row[0]:
                raw = row[0]
                data = json.loads(raw) if isinstance(raw, str) else (raw or {})
            data["last_processed_date"] = date
            payload = json.dumps(data, ensure_ascii=False)
            conn.execute(
                "INSERT INTO storage_settings (id, data_json) VALUES (?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data_json = excluded.data_json",
                (_GRAPH_META_ID, payload),
            )
            conn.commit()
        finally:
            pass

    def get_processed_dates_for_chat(self, chat_id: int) -> set[str]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT processed_date FROM processed_dates WHERE chat_id = ?",
                (int(chat_id),),
            ).fetchall()
            return {r[0] for r in rows}
        except Exception:
            return set()
    def set_processed_date(self, chat_id: int, date: str) -> None:
        if not storage_db_writes_enabled():
            return
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO processed_dates (chat_id, processed_date) VALUES (?, ?)",
                (int(chat_id), date),
            )
            conn.commit()
        except Exception as e:
            logger.debug("set_processed_date: %s", e)
    def get_global_settings(self) -> dict:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT data_json FROM storage_settings WHERE id = 1"
            ).fetchone()
            if not row:
                return {}
            raw = row[0]
            return json.loads(raw) if isinstance(raw, str) else (raw or {})
        finally:
            pass

    def set_global_settings(self, data: dict) -> None:
        if not storage_db_writes_enabled():
            return
        conn = self._conn()
        try:
            payload = json.dumps(data, ensure_ascii=False)
            conn.execute(
                "INSERT INTO storage_settings (id, data_json) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET data_json = excluded.data_json",
                (payload,),
            )
            conn.commit()
        finally:
            pass


def get_storage() -> SqliteStorage | None:
    """Return SqliteStorage instance when DB storage is enabled, else None."""
    from services.storage_cutover import storage_db_reads_enabled
    if not storage_db_reads_enabled():
        return None
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = SqliteStorage()
    return _storage_instance


_storage_instance: SqliteStorage | None = None


def init_storage() -> SqliteStorage | None:
    """Initialize storage and ensure tables exist. Call at bot startup."""
    global _storage_instance
    from services.storage_cutover import storage_db_reads_enabled
    if not storage_db_reads_enabled():
        return None
    _storage_instance = SqliteStorage()
    conn = _storage_instance._conn()
    _ensure_tables(conn)
    conn.commit()
    return _storage_instance
