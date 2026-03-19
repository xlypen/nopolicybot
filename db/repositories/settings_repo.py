from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ChatSettings


class SettingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, chat_id: int) -> dict:
        result = await self.session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
        row = result.scalar_one_or_none()
        return row.settings if row else {}

    async def set(self, chat_id: int, settings: dict):
        result = await self.session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
        row = result.scalar_one_or_none()
        if row:
            row.settings = settings
        else:
            self.session.add(ChatSettings(chat_id=chat_id, settings=settings))
        await self.session.flush()
