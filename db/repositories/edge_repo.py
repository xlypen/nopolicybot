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

    async def get_all_chats(self) -> list[Edge]:
        result = await self.session.execute(select(Edge))
        return result.scalars().all()

    async def upsert(
        self,
        chat_id: int,
        from_user: int,
        to_user: int,
        weight_delta: float = 1.0,
        period: str = "7d",
        *,
        tone: str | None = None,
        topics: list[str] | None = None,
        summary: str | None = None,
        summary_by_date: list[dict] | None = None,
    ) -> Edge:
        existing = await self.session.execute(
            select(Edge).where(Edge.chat_id == chat_id, Edge.from_user == from_user, Edge.to_user == to_user)
        )
        edge = existing.scalar_one_or_none()
        if edge:
            edge.weight += weight_delta
            edge.last_updated = datetime.utcnow()
            edge.period_7d += weight_delta
            edge.period_30d += weight_delta
            if tone is not None:
                edge.tone = tone
            if topics is not None:
                edge.topics = topics
            if summary is not None:
                edge.summary = summary
            if summary_by_date is not None:
                edge.summary_by_date = summary_by_date
        else:
            edge = Edge(
                chat_id=chat_id,
                from_user=from_user,
                to_user=to_user,
                weight=weight_delta,
                period_7d=weight_delta,
                period_30d=weight_delta,
                tone=tone or "neutral",
                topics=topics or [],
                summary=summary or "",
                summary_by_date=summary_by_date or [],
            )
            self.session.add(edge)
        await self.session.flush()
        return edge

    async def upsert_full(
        self,
        chat_id: int,
        from_user: int,
        to_user: int,
        *,
        weight: float = 0,
        period_7d: float = 0,
        period_30d: float = 0,
        tone: str = "neutral",
        topics: list[str] | None = None,
        summary: str = "",
        summary_by_date: list[dict] | None = None,
    ) -> Edge:
        """Full upsert — replaces all fields (used for migration and sync from social_graph)."""
        existing = await self.session.execute(
            select(Edge).where(Edge.chat_id == chat_id, Edge.from_user == from_user, Edge.to_user == to_user)
        )
        edge = existing.scalar_one_or_none()
        if edge:
            edge.weight = max(edge.weight, weight)
            edge.period_7d = max(edge.period_7d, period_7d)
            edge.period_30d = max(edge.period_30d, period_30d)
            edge.tone = tone
            edge.topics = topics or []
            edge.summary = summary
            edge.summary_by_date = summary_by_date or []
            edge.last_updated = datetime.utcnow()
        else:
            edge = Edge(
                chat_id=chat_id,
                from_user=from_user,
                to_user=to_user,
                weight=weight,
                period_7d=period_7d,
                period_30d=period_30d,
                tone=tone,
                topics=topics or [],
                summary=summary,
                summary_by_date=summary_by_date or [],
            )
            self.session.add(edge)
        await self.session.flush()
        return edge
