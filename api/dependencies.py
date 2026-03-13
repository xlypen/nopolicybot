import os
from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import AsyncSessionLocal
from db.repositories.edge_repo import EdgeRepository
from db.repositories.message_repo import MessageRepository
from db.repositories.settings_repo import SettingsRepository
from db.repositories.user_repo import UserRepository

security = HTTPBearer(auto_error=False)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "change-me-in-production")


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


def get_edge_repo(session: AsyncSession = Depends(get_db_session)) -> EdgeRepository:
    return EdgeRepository(session)


def get_user_repo(session: AsyncSession = Depends(get_db_session)) -> UserRepository:
    return UserRepository(session)


def get_message_repo(session: AsyncSession = Depends(get_db_session)) -> MessageRepository:
    return MessageRepository(session)


def get_settings_repo(session: AsyncSession = Depends(get_db_session)) -> SettingsRepository:
    return SettingsRepository(session)


def require_auth(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials or credentials.credentials != ADMIN_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return credentials.credentials
