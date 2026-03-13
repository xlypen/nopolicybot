from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Message


class MessageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, **kwargs) -> Message:
        msg = Message(**kwargs)
        self.session.add(msg)
        await self.session.flush()
        return msg

    async def get_by_period(self, chat_id: int, days: int) -> list[Message]:
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(Message)
            .where(Message.chat_id == chat_id)
            .where(Message.sent_at >= since)
            .order_by(Message.sent_at.desc())
        )
        return result.scalars().all()

    async def get_texts_by_community(self, chat_id: int, user_ids: list[int], days: int = 30) -> list[str]:
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(Message.text)
            .where(Message.chat_id == chat_id)
            .where(Message.user_id.in_(user_ids))
            .where(Message.sent_at >= since)
            .where(Message.text.isnot(None))
        )
        return [r[0] for r in result.all()]

    async def count_by_user(self, user_id: int, chat_id: int, days: int) -> int:
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(func.count(Message.id))
            .where(Message.user_id == user_id)
            .where(Message.chat_id == chat_id)
            .where(Message.sent_at >= since)
        )
        return result.scalar() or 0

    async def get_daily_counts(self, chat_id: int, days: int = 30) -> list[dict]:
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(func.date(Message.sent_at).label("date"), func.count(Message.id).label("count"))
            .where(Message.chat_id == chat_id)
            .where(Message.sent_at >= since)
            .group_by(func.date(Message.sent_at))
            .order_by(func.date(Message.sent_at))
        )
        return [{"date": r.date, "count": r.count} for r in result.all()]

    async def get_active_user_ids(self, chat_id: int, days: int) -> list[int]:
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(Message.user_id)
            .where(Message.chat_id == chat_id)
            .where(Message.sent_at >= since)
            .distinct()
        )
        return [r[0] for r in result.all()]
