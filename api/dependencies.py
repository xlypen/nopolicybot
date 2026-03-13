import os
import hmac
from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from db.engine import AsyncSessionLocal
from db.repositories.edge_repo import EdgeRepository
from db.repositories.message_repo import MessageRepository
from db.repositories.settings_repo import SettingsRepository
from db.repositories.user_repo import UserRepository
from services.audit_log import write_event

security = HTTPBearer(auto_error=False)


def _admin_token() -> str:
    return str(os.getenv("ADMIN_TOKEN", "change-me-in-production")).strip()


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
    token = _admin_token()
    supplied = str(credentials.credentials).strip() if credentials else ""
    if not supplied or not token or not hmac.compare_digest(supplied, token):
        write_event("api_auth_failed", severity="warning", source="api_v2", payload={"has_credentials": bool(credentials)})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return supplied
