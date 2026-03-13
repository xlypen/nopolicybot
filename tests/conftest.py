import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.models import Base
from db.repositories.edge_repo import EdgeRepository
from db.repositories.message_repo import MessageRepository
from db.repositories.user_repo import UserRepository


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def message_repo(db_session):
    return MessageRepository(db_session)


@pytest.fixture
def edge_repo(db_session):
    return EdgeRepository(db_session)


@pytest.fixture
def user_repo(db_session):
    return UserRepository(db_session)


def build_graph_payload(n_users=10, n_edges=20):
    nodes = [
        {
            "id": i + 1,
            "degree": (i % 5) + 1,
            "messages_7d": 5 + i,
            "messages_30d": 20 + i,
            "influence_score": round(((i % 10) / 10), 3),
            "centrality": 0.1,
            "community_id": i % 3,
            "tier": "core" if i < 3 else "secondary",
        }
        for i in range(n_users)
    ]
    edges = [{"source": (i % n_users) + 1, "target": ((i + 1) % n_users) + 1, "weight": 1.0} for i in range(n_edges)]
    return {"nodes": nodes, "edges": edges}


@pytest.fixture
async def sample_messages(db_session):
    repo = MessageRepository(db_session)
    rows = []
    for i in range(30):
        rows.append(
            await repo.add(
                chat_id=1,
                user_id=(i % 5) + 1,
                text=f"msg-{i}",
                media_type="text",
                sent_at=datetime.utcnow() - timedelta(days=i % 7),
            )
        )
    await db_session.commit()
    return rows
