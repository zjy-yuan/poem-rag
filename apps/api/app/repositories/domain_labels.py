from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.text import normalize_lookup
from app.models.domain_label import (
    DomainLabel,
    DomainLabelAlias,
    PoemVersionDomainLabel,
)
from app.models.poem import Poem, PoemStatus
from app.models.version import PoemVersion


class DomainLabelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _label_options():
        return selectinload(DomainLabel.aliases)

    @staticmethod
    def _assignment_options():
        return (
            selectinload(PoemVersionDomainLabel.version),
            selectinload(PoemVersionDomainLabel.domain_label).selectinload(
                DomainLabel.aliases
            ),
        )

    @staticmethod
    def _like_pattern(value: str) -> str:
        normalized = normalize_lookup(value)
        escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    async def list_labels(
        self,
        *,
        page: int,
        page_size: int,
        dimension: str | None = None,
        status: str | None = None,
        q: str | None = None,
    ) -> tuple[list[DomainLabel], int]:
        filters: list[object] = []
        if dimension is not None:
            filters.append(DomainLabel.dimension == dimension)
        if status is not None:
            filters.append(DomainLabel.status == status)

        normalized_query = normalize_lookup(q or "")
        if normalized_query:
            pattern = self._like_pattern(normalized_query)
            filters.append(
                or_(
                    DomainLabel.normalized_name.like(pattern, escape="\\"),
                    DomainLabel.id.in_(
                        select(DomainLabelAlias.domain_label_id).where(
                            DomainLabelAlias.normalized_alias.like(pattern, escape="\\")
                        )
                    ),
                )
            )

        total = await self.session.scalar(select(func.count(DomainLabel.id)).where(*filters))
        statement = (
            select(DomainLabel)
            .where(*filters)
            .options(self._label_options())
            .order_by(
                DomainLabel.dimension.asc(),
                DomainLabel.canonical_name.asc(),
                DomainLabel.id.asc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().unique()), int(total or 0)

    async def get_label(self, label_id: int) -> DomainLabel | None:
        statement = (
            select(DomainLabel)
            .where(DomainLabel.id == label_id)
            .options(self._label_options())
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_label_by_normalized_name(
        self,
        *,
        dimension: str,
        normalized_name: str,
    ) -> DomainLabel | None:
        statement = (
            select(DomainLabel)
            .where(
                DomainLabel.dimension == dimension,
                DomainLabel.normalized_name == normalized_name,
            )
            .options(self._label_options())
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    def create_label(
        self,
        *,
        dimension: str,
        canonical_name: str,
        normalized_name: str,
        description: str | None,
    ) -> DomainLabel:
        label = DomainLabel(
            dimension=dimension,
            canonical_name=canonical_name,
            normalized_name=normalized_name,
            description=description,
        )
        self.session.add(label)
        return label

    async def list_assignments(
        self,
        *,
        label_id: int,
        page: int,
        page_size: int,
        review_status: str | None = None,
        generation_method: str | None = None,
    ) -> tuple[list[PoemVersionDomainLabel], int]:
        filters: list[object] = [PoemVersionDomainLabel.domain_label_id == label_id]
        if review_status is not None:
            filters.append(PoemVersionDomainLabel.review_status == review_status)
        if generation_method is not None:
            filters.append(PoemVersionDomainLabel.generation_method == generation_method)
        return await self._list_assignments(
            filters=filters,
            page=page,
            page_size=page_size,
        )

    async def list_version_assignments(
        self,
        *,
        version_id: int,
        page: int,
        page_size: int,
        review_status: str | None = None,
        generation_method: str | None = None,
    ) -> tuple[list[PoemVersionDomainLabel], int]:
        filters: list[object] = [
            PoemVersionDomainLabel.poem_version_id == version_id
        ]
        if review_status is not None:
            filters.append(PoemVersionDomainLabel.review_status == review_status)
        if generation_method is not None:
            filters.append(PoemVersionDomainLabel.generation_method == generation_method)
        return await self._list_assignments(
            filters=filters,
            page=page,
            page_size=page_size,
        )

    async def _list_assignments(
        self,
        *,
        filters: list[object],
        page: int,
        page_size: int,
    ) -> tuple[list[PoemVersionDomainLabel], int]:
        total = await self.session.scalar(
            select(func.count(PoemVersionDomainLabel.id)).where(*filters)
        )
        statement = (
            select(PoemVersionDomainLabel)
            .where(*filters)
            .options(*self._assignment_options())
            .order_by(
                PoemVersionDomainLabel.created_at.desc(),
                PoemVersionDomainLabel.id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(statement)
        return list(result.scalars().unique()), int(total or 0)

    async def get_assignment(
        self,
        assignment_id: int,
    ) -> PoemVersionDomainLabel | None:
        statement = (
            select(PoemVersionDomainLabel)
            .where(PoemVersionDomainLabel.id == assignment_id)
            .options(*self._assignment_options())
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_assignment_by_origin(
        self,
        *,
        poem_version_id: int,
        label_id: int,
        origin_ref: str,
    ) -> PoemVersionDomainLabel | None:
        statement = (
            select(PoemVersionDomainLabel)
            .where(
                PoemVersionDomainLabel.poem_version_id == poem_version_id,
                PoemVersionDomainLabel.domain_label_id == label_id,
                PoemVersionDomainLabel.origin_ref == origin_ref,
            )
            .options(*self._assignment_options())
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_current_version(
        self,
        poem_id: int,
        *,
        public_only: bool = False,
    ) -> PoemVersion | None:
        filters: list[object] = [
            Poem.id == poem_id,
            PoemVersion.poem_id == Poem.id,
            PoemVersion.version_no == Poem.version_no,
        ]
        if public_only:
            filters.extend(
                [
                    Poem.status == PoemStatus.PUBLISHED.value,
                    Poem.deleted_at.is_(None),
                ]
            )
        statement = select(PoemVersion).join(Poem).where(*filters)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_approved_for_version(
        self,
        version_id: int,
    ) -> list[PoemVersionDomainLabel]:
        statement = (
            select(PoemVersionDomainLabel)
            .where(
                PoemVersionDomainLabel.poem_version_id == version_id,
                PoemVersionDomainLabel.review_status == "approved",
            )
            .options(
                selectinload(PoemVersionDomainLabel.domain_label).selectinload(
                    DomainLabel.merged_into
                )
            )
            .order_by(
                PoemVersionDomainLabel.generation_method.asc(),
                PoemVersionDomainLabel.id.asc(),
            )
        )
        result = await self.session.execute(statement)
        return list(result.scalars().unique())
