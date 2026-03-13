from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, user_id: int, chat_id: int, **kwargs) -> User:
        result = await self.session.execute(select(User).where(User.id == user_id, User.chat_id == chat_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(id=user_id, chat_id=chat_id, **kwargs)
            self.session.add(user)
            await self.session.flush()
        else:
            user.last_seen = datetime.utcnow()
        return user

    async def get_all(self, chat_id: int) -> list[User]:
        result = await self.session.execute(select(User).where(User.chat_id == chat_id, User.is_active == True))  # noqa: E712
        return result.scalars().all()
