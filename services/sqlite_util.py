"""Общие настройки SQLite при параллельном доступе (бот, API, админка)."""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

_BUSY_TIMEOUT_SEC = float(os.getenv("SQLITE_BUSY_TIMEOUT_SEC", "30"))
_LOCK_RETRY_ATTEMPTS = int(os.getenv("SQLITE_LOCK_RETRY_ATTEMPTS", "5"))
_LOCK_RETRY_BASE_SLEEP = float(os.getenv("SQLITE_LOCK_RETRY_BASE_SLEEP", "0.05"))

_logger = logging.getLogger(__name__)

T = TypeVar("T")


def sqlite_busy_timeout_sec() -> float:
    return _BUSY_TIMEOUT_SEC


def sqlite_connect(
    database: str | Path,
    *,
    timeout: float | None = None,
    **kwargs: Any,
) -> sqlite3.Connection:
    """Открыть БД с ожиданием снятия блокировки + WAL/NORMAL для всех процессов.

    Все вызовы (бот, API, админка, скрипты) обязаны проходить через эту
    функцию: иначе при параллельной записи будут срабатывать долгие
    блокировки и `database is locked` (см. инцидент 2026-04-29).
    """
    t = _BUSY_TIMEOUT_SEC if timeout is None else timeout
    if "check_same_thread" not in kwargs:
        kwargs["check_same_thread"] = False
    conn = sqlite3.connect(str(database), timeout=t, **kwargs)
    conn.execute(f"PRAGMA busy_timeout={int(float(t) * 1000)}")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
    except sqlite3.DatabaseError:
        # для ро-инструментов (sqlite3 CLI) PRAGMA может быть запрещён
        pass
    return conn


def with_lock_retry(
    fn: Callable[[], T],
    *,
    attempts: int | None = None,
    base_sleep: float | None = None,
    op_name: str = "sqlite",
) -> T:
    """Выполнить sqlite-операцию с ретраями на 'database is locked'.

    SQLite WAL не блокирует читателей, но при апгрейде deferred-tx
    (SELECT → INSERT) может вернуть SQLITE_BUSY мгновенно, минуя
    busy_timeout. Этот ретрай — защита от такой гонки.
    """
    n = _LOCK_RETRY_ATTEMPTS if attempts is None else attempts
    base = _LOCK_RETRY_BASE_SLEEP if base_sleep is None else base_sleep
    last_err: Exception | None = None
    for i in range(max(1, n)):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            last_err = e
            if i + 1 < n:
                time.sleep(base * (2**i))
                _logger.debug("%s locked, retry %d/%d", op_name, i + 1, n)
    assert last_err is not None
    raise last_err
