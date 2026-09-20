from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.text import normalize_lookup
from app.models.author import Author
from app.models.category import Category
from app.models.dynasty import Dynasty
from app.models.poem import Poem, PoemCategory, PoemStatus
from app.models.tag import PoemTag, Tag


class PoemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _with_relations(self):
        return (
            selectinload(Poem.author),
            selectinload(Poem.dynasty),
            selectinload(Poem.category_links).selectinload(PoemCategory.category),
            selectinload(Poem.tag_links).selectinload(PoemTag.tag),
        )

    def _filters(
        self,
        *,
        q: str | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
        category_id: int | None = None,
    ) -> list[object]:
        filters: list[object] = []
        if author_id is not None:
            filters.append(Poem.author_id == author_id)
        if dynasty_id is not None:
            filters.append(Poem.dynasty_id == dynasty_id)
        if category_id is not None:
            filters.append(
                Poem.id.in_(
                    select(PoemCategory.poem_id).where(PoemCategory.category_id == category_id)
                )
            )

        normalized_query = normalize_lookup(q or "")
        if normalized_query:
            escaped_query = (
                normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            pattern = f"%{escaped_query}%"
            filters.append(
                or_(
                    func.lower(Poem.title).like(pattern, escape="\\"),
                    func.lower(Poem.content).like(pattern, escape="\\"),
                    Poem.author_id.in_(
                        select(Author.id).where(
                            func.lower(Author.name).like(pattern, escape="\\")
                        )
                    ),
                    Poem.dynasty_id.in_(
                        select(Dynasty.id).where(
                            func.lower(Dynasty.name).like(pattern, escape="\\")
                        )
                    ),
                    Poem.id.in_(
                        select(PoemCategory.poem_id)
                        .join(Category, Category.id == PoemCategory.category_id)
                        .where(func.lower(Category.name).like(pattern, escape="\\"))
                    ),
                    Poem.id.in_(
                        select(PoemTag.poem_id)
                        .join(Tag, Tag.id == PoemTag.tag_id)
                        .where(func.lower(Tag.name).like(pattern, escape="\\"))
                    ),
                )
            )
        return filters

    async def list_public(
        self,
        *,
        page: int,
        page_size: int,
        q: str | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
        category_id: int | None = None,
    ) -> tuple[list[Poem], int]:
        filters = [
            Poem.status == PoemStatus.PUBLISHED.value,
            Poem.deleted_at.is_(None),
            *self._filters(
                q=q,
                author_id=author_id,
                dynasty_id=dynasty_id,
                category_id=category_id,
            ),
        ]
        total = await self.session.scalar(select(func.count(Poem.id)).where(*filters))
        statement = (
            select(Poem)
            .where(*filters)
            .options(*self._with_relations())
            .order_by(Poem.published_at.desc(), Poem.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().unique()), int(total or 0)

    async def list_admin(
        self,
        *,
        page: int,
        page_size: int,
        status: str | None = None,
        include_deleted: bool = False,
        q: str | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
        category_id: int | None = None,
    ) -> tuple[list[Poem], int]:
        filters: list[object] = []
        if status is not None:
            filters.append(Poem.status == status)
        if not include_deleted:
            filters.append(Poem.deleted_at.is_(None))
        filters.extend(
            self._filters(
                q=q,
                author_id=author_id,
                dynasty_id=dynasty_id,
                category_id=category_id,
            )
        )
        total = await self.session.scalar(select(func.count(Poem.id)).where(*filters))
        statement = (
            select(Poem)
            .where(*filters)
            .options(*self._with_relations())
            .order_by(Poem.updated_at.desc(), Poem.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().unique()), int(total or 0)

    async def get_public(self, poem_id: int) -> Poem | None:
        statement = (
            select(Poem)
            .where(
                Poem.id == poem_id,
                Poem.status == PoemStatus.PUBLISHED.value,
                Poem.deleted_at.is_(None),
            )
            .options(*self._with_relations())
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_admin(self, poem_id: int) -> Poem | None:
        statement = select(Poem).where(Poem.id == poem_id).options(*self._with_relations())
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_by_author_public(self, author_id: int) -> list[Poem]:
        statement = (
            select(Poem)
            .where(
                Poem.author_id == author_id,
                Poem.status == PoemStatus.PUBLISHED.value,
                Poem.deleted_at.is_(None),
            )
            .options(*self._with_relations())
            .order_by(Poem.published_at.desc(), Poem.id.desc())
        )
        result = await self.session.execute(statement)
        return list(result.scalars().unique())

    def create(
        self,
        *,
        title: str,
        content: str,
        normalized_content: str,
        summary: str | None,
        author_id: int | None,
        dynasty_id: int | None,
    ) -> Poem:
        poem = Poem(
            title=title,
            content=content,
            normalized_content=normalized_content,
            summary=summary,
            author_id=author_id,
            dynasty_id=dynasty_id,
            status=PoemStatus.DRAFT.value,
        )
        self.session.add(poem)
        return poem

    async def get_tags(self, normalized_names: Sequence[str]) -> dict[str, Tag]:
        if not normalized_names:
            return {}
        result = await self.session.execute(
            select(Tag).where(Tag.normalized_name.in_(normalized_names))
        )
        return {tag.normalized_name: tag for tag in result.scalars()}

    async def search_text(self, query: str, limit: int) -> list[Poem]:
        return (await self.list_public(page=1, page_size=limit, q=query))[0]
