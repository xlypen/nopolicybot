import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/bot.db")

_engine_kwargs: dict = {"echo": False}
if "sqlite" in DATABASE_URL:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_size"] = int(os.getenv("DB_POOL_SIZE", "5"))
    _engine_kwargs["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "10"))

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    os.makedirs("data", exist_ok=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception:
        pass


@asynccontextmanager
async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
