from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.text import normalize_lookup
from app.models.author import Author
from app.models.poem import Poem, PoemStatus


class AuthorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _counts(self, *, public_only: bool):
        filters = [Poem.deleted_at.is_(None)]
        if public_only:
            filters.append(Poem.status == PoemStatus.PUBLISHED.value)
        return (
            select(Poem.author_id, func.count(Poem.id).label("poem_count"))
            .where(*filters)
            .group_by(Poem.author_id)
            .subquery()
        )

    async def list(
        self,
        *,
        page: int,
        page_size: int,
        q: str | None = None,
        dynasty_id: int | None = None,
        public_only: bool = True,
    ) -> tuple[list[tuple[Author, int]], int]:
        filters = [Author.deleted_at.is_(None)]
        if dynasty_id is not None:
            filters.append(Author.dynasty_id == dynasty_id)
        normalized_query = normalize_lookup(q or "")
        if normalized_query:
            filters.append(func.lower(Author.name).like(f"%{normalized_query}%"))

        total = await self.session.scalar(select(func.count(Author.id)).where(*filters))
        counts = self._counts(public_only=public_only)
        statement = (
            select(Author, func.coalesce(counts.c.poem_count, 0))
            .outerjoin(counts, counts.c.author_id == Author.id)
            .where(*filters)
            .options(selectinload(Author.dynasty))
            .order_by(Author.name.asc(), Author.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(statement)
        return [(author, int(count)) for author, count in result.all()], int(total or 0)

    async def poem_count(self, author_id: int, *, public_only: bool = False) -> int:
        filters = [Poem.author_id == author_id, Poem.deleted_at.is_(None)]
        if public_only:
            filters.append(Poem.status == PoemStatus.PUBLISHED.value)
        total = await self.session.scalar(select(func.count(Poem.id)).where(*filters))
        return int(total or 0)

    async def get(self, author_id: int, *, include_deleted: bool = False) -> Author | None:
        statement = (
            select(Author).where(Author.id == author_id).options(selectinload(Author.dynasty))
        )
        if not include_deleted:
            statement = statement.where(Author.deleted_at.is_(None))
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_normalized_name(self, normalized_name: str) -> Author | None:
        result = await self.session.execute(
            select(Author).where(Author.normalized_name == normalized_name)
        )
        return result.scalar_one_or_none()

    def create(self, *, name: str, normalized_name: str) -> Author:
        author = Author(name=name, normalized_name=normalized_name, aliases=[])
        self.session.add(author)
        return author

    async def delete(self, author: Author) -> None:
        from datetime import UTC, datetime

        author.deleted_at = datetime.now(UTC)
