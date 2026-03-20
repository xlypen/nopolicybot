"""
Storage abstraction layer. IStorage interface for user_stats, social_graph, bot_settings.
Single DB: data/bot.db.
"""

from __future__ import annotations

from typing import Protocol


class IStorage(Protocol):
    """Interface for persistent storage (user profiles, messages, chats, graph, settings)."""

    def get_user_profile(self, user_id: int) -> dict | None:
        """Return user profile dict or None if not found."""
        ...

    def set_user_profile(self, user_id: int, profile: dict) -> None:
        """Upsert user profile."""
        ...

    def get_user_messages(
        self, user_id: int, chat_id: int | None = None, limit: int = 1000
    ) -> list[dict]:
        """Return messages: [{text, date}] or [{text, date, chat_id}] from messages table."""
        ...

    def get_display_names(self) -> dict[str, str]:
        """Return {user_id_str: display_name} for all users."""
        ...

    def get_users_in_chat(self, chat_id: int) -> list[int]:
        """Return list of user_ids who have messages in chat."""
        ...

    def increment_warnings(self, user_id: int) -> None:
        """Increment warnings_received counter for user."""
        ...

    def append_message(
        self, user_id: int, chat_id: int, text: str, date: str, *, dedupe: bool = True
    ) -> bool:
        """Append message. Returns True if inserted, False if skipped (e.g. duplicate)."""
        ...

    def get_chat(self, chat_id: int) -> dict | None:
        """Return chat {chat_id, title, last_seen} or None."""
        ...

    def upsert_chat(self, chat_id: int, title: str) -> None:
        """Upsert chat."""
        ...

    def list_storage_chats(self) -> list[dict]:
        """All rows from storage_chats: [{chat_id, title, last_seen}, ...]."""
        ...

    def iter_user_profiles(self) -> list[tuple[int, dict]]:
        """(user_id, profile dict) for all user_profiles rows."""
        ...

    def delete_user_message_archive(self, user_id: int, chat_id: int | None = None) -> int:
        """Delete archive rows; chat_id None = all chats. Returns deleted count."""
        ...

    def get_graph_meta(self) -> dict:
        """Graph metadata blob (last_processed_date, realtime_cursors, ...)."""
        ...

    def replace_graph_meta(self, data: dict) -> None:
        """Replace graph metadata blob entirely."""
        ...

    def append_dialogue_message(
        self,
        chat_id: int,
        date: str,
        sender_id: int,
        sender_name: str,
        text: str,
        reply_to_user_id: int | None = None,
    ) -> None: ...

    def get_dialogue_messages(self, chat_id: int, date: str) -> list[dict]: ...

    def get_distinct_dialogue_dates(self, chat_id: int, before_date: str) -> list[str]: ...

    def get_all_dialogue_chat_ids(self) -> list[int]: ...

    def delete_dialogue_before(self, chat_id: int, cutoff_date: str) -> int: ...

    def get_connection(self, chat_id: int, pair_key: str) -> dict | None:
        """Return connection data for pair_key (e.g. '123|456') or None."""
        ...

    def upsert_connection(self, chat_id: int, pair_key: str, data: dict) -> None:
        """Upsert connection."""
        ...

    def get_all_connections(self, chat_id: int | None) -> list[dict]:
        """Return all connections, optionally filtered by chat_id."""
        ...

    def get_last_processed_date(self) -> str | None:
        """Return last processed date (YYYY-MM-DD) for social graph."""
        ...

    def set_last_processed_date(self, date: str) -> None:
        """Set last processed date."""
        ...

    def get_processed_dates_for_chat(self, chat_id: int) -> set[str]:
        """Return set of processed dates for chat."""
        ...

    def set_processed_date(self, chat_id: int, date: str) -> None:
        """Mark date as processed for chat."""
        ...

    def get_global_settings(self) -> dict:
        """Return global bot settings (merged with defaults)."""
        ...

    def set_global_settings(self, data: dict) -> None:
        """Replace global settings (only keys from DEFAULTS)."""
        ...
