from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import normalize_lookup
from app.models.dynasty import Dynasty
from app.models.poem import Poem


class DynastyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self) -> list[Dynasty]:
        result = await self.session.execute(
            select(Dynasty).order_by(Dynasty.sort_order.asc(), Dynasty.id.asc())
        )
        return list(result.scalars())

    async def get(self, dynasty_id: int) -> Dynasty | None:
        return await self.session.get(Dynasty, dynasty_id)

    async def get_by_normalized_name(self, normalized_name: str) -> Dynasty | None:
        result = await self.session.execute(
            select(Dynasty).where(Dynasty.normalized_name == normalized_name)
        )
        return result.scalar_one_or_none()

    async def poem_count(self, dynasty_id: int) -> int:
        total = await self.session.scalar(
            select(func.count(Poem.id)).where(Poem.dynasty_id == dynasty_id)
        )
        return int(total or 0)

    def create(self, *, name: str, normalized_name: str) -> Dynasty:
        dynasty = Dynasty(name=name, normalized_name=normalized_name)
        self.session.add(dynasty)
        return dynasty

    async def delete(self, dynasty: Dynasty) -> None:
        await self.session.delete(dynasty)


def normalized_name(value: str) -> str:
    return normalize_lookup(value)
