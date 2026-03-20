"""UTC datetimes for TIMESTAMP WITHOUT TIME ZONE (SQLite + PostgreSQL/asyncpg)."""
from __future__ import annotations

from datetime import datetime, timezone


def to_naive_utc(dt: datetime | None = None) -> datetime:
    """Return naive datetime in UTC. asyncpg rejects mixing aware/naive for TIMESTAMP columns."""
    if dt is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)
