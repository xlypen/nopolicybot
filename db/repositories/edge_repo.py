from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Edge


class EdgeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(self, chat_id: int) -> list[Edge]:
        result = await self.session.execute(select(Edge).where(Edge.chat_id == chat_id))
        return result.scalars().all()

    async def upsert(self, chat_id: int, from_user: int, to_user: int, weight_delta: float = 1.0, period: str = "7d"):
        existing = await self.session.execute(
            select(Edge).where(Edge.chat_id == chat_id, Edge.from_user == from_user, Edge.to_user == to_user)
        )
        edge = existing.scalar_one_or_none()
        if edge:
            edge.weight += weight_delta
            edge.last_updated = datetime.utcnow()
            if period == "7d":
                edge.period_7d += weight_delta
            else:
                edge.period_30d += weight_delta
        else:
            edge = Edge(
                chat_id=chat_id,
                from_user=from_user,
                to_user=to_user,
                weight=weight_delta,
                period_7d=weight_delta if period == "7d" else 0,
                period_30d=weight_delta if period == "30d" else 0,
            )
            self.session.add(edge)
        await self.session.flush()
        return edge
