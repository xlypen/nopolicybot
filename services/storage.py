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
        """Return messages for user: [{text, date}] or [{text, date, chat_id}] when chat_id is None."""
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

    def get_dialogue_log(self, chat_id: int) -> dict:
        """Return dialogue_log for chat: {date: [msgs], ...}."""
        ...

    def set_dialogue_log(self, chat_id: int, log: dict) -> None:
        """Replace dialogue_log for chat."""
        ...

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
