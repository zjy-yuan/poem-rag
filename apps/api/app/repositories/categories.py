from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.poem import Poem, PoemCategory, PoemStatus


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(
        self,
        *,
        category_type: str | None = None,
        include_inactive: bool = False,
        public_only: bool = True,
    ) -> list[tuple[Category, int]]:
        filters: list[object] = []
        if not include_inactive:
            filters.append(Category.is_active.is_(True))
        if category_type is not None:
            filters.append(Category.type == category_type)

        count_filters = [Poem.deleted_at.is_(None)]
        if public_only:
            count_filters.append(Poem.status == PoemStatus.PUBLISHED.value)
        counts = (
            select(PoemCategory.category_id, func.count(Poem.id).label("poem_count"))
            .join(Poem, Poem.id == PoemCategory.poem_id)
            .where(*count_filters)
            .group_by(PoemCategory.category_id)
            .subquery()
        )
        statement = (
            select(Category, func.coalesce(counts.c.poem_count, 0))
            .outerjoin(counts, counts.c.category_id == Category.id)
            .where(*filters)
            .order_by(Category.sort_order.asc(), Category.id.asc())
        )
        result = await self.session.execute(statement)
        return [(category, int(count)) for category, count in result.all()]

    async def get(self, category_id: int) -> Category | None:
        return await self.session.get(Category, category_id)

    async def get_by_normalized_name(
        self,
        normalized_name: str,
        category_type: str,
    ) -> Category | None:
        result = await self.session.execute(
            select(Category).where(
                Category.normalized_name == normalized_name,
                Category.type == category_type,
            )
        )
        return result.scalar_one_or_none()

    async def has_children(self, category_id: int) -> bool:
        child_id = await self.session.scalar(
            select(Category.id).where(Category.parent_id == category_id).limit(1)
        )
        return child_id is not None

    def create(self, *, name: str, normalized_name: str, category_type: str) -> Category:
        category = Category(
            name=name,
            normalized_name=normalized_name,
            type=category_type,
        )
        self.session.add(category)
        return category
