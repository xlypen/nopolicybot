"""Синхронный SQLAlchemy engine для метрик и отчётов (без asyncio.run в event loop)."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.engine import DATABASE_URL

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def sync_database_url() -> str:
    """Преобразует async URL в sync (sqlite / psycopg2)."""
    url = (os.getenv("DATABASE_URL") or DATABASE_URL or "").strip()
    if not url:
        return "sqlite:///./data/bot.db"
    if "+aiosqlite" in url:
        return url.replace("+aiosqlite", "", 1)
    if "+asyncpg" in url:
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    return url


def get_sync_engine():
    global _engine
    if _engine is None:
        sync_url = sync_database_url()
        kwargs: dict = {"echo": False}
        if sync_url.startswith("sqlite"):
            _t = float(os.getenv("SQLITE_BUSY_TIMEOUT_SEC", "30"))
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": _t}
        else:
            kwargs["pool_pre_ping"] = True
            kwargs["pool_size"] = int(os.getenv("DB_POOL_SIZE", "5"))
            kwargs["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "10"))
            _recycle = int(os.getenv("DB_POOL_RECYCLE_SEC", "3600"))
            if _recycle > 0:
                kwargs["pool_recycle"] = _recycle
        try:
            _engine = create_engine(sync_url, **kwargs)
        except Exception as e:
            logger.warning("sync engine create failed url=%s: %s", sync_url.split("@")[-1], e)
            raise
    return _engine


def get_sync_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_sync_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


@contextmanager
def sync_session_scope() -> Generator[Session, None, None]:
    factory = get_sync_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
