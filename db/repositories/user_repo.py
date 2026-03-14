from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(self, user_id: int, chat_id: int, **kwargs) -> User:
        result = await self.session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            # Schema currently keeps one User row per telegram user id.
            # Use nested transaction to avoid blowing up the whole ingest tx on races/duplicates.
            created = None
            try:
                async with self.session.begin_nested():
                    created = User(id=int(user_id), chat_id=int(chat_id), **kwargs)
                    self.session.add(created)
                    await self.session.flush()
            except IntegrityError:
                created = None
            if created is not None:
                user = created
            else:
                result = await self.session.execute(select(User).where(User.id == user_id))
                user = result.scalar_one_or_none()
                if not user:
                    raise RuntimeError(f"failed to upsert user id={user_id}")

        # Keep latest chat/activity and refresh known profile fields.
        user.chat_id = int(chat_id)
        for field in ("username", "first_name", "last_name"):
            val = kwargs.get(field)
            if val is None:
                continue
            text = str(val).strip()
            if text:
                setattr(user, field, text[:200])
        if "is_active" in kwargs:
            user.is_active = bool(kwargs.get("is_active"))
        user.last_seen = kwargs.get("last_seen") or datetime.utcnow()
        return user

    async def get_all(self, chat_id: int) -> list[User]:
        result = await self.session.execute(select(User).where(User.chat_id == chat_id, User.is_active == True))  # noqa: E712
        return result.scalars().all()

    async def get_all_active(self) -> list[User]:
        result = await self.session.execute(select(User).where(User.is_active == True))  # noqa: E712
        return result.scalars().all()
