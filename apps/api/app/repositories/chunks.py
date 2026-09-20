from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.text import normalize_content, normalize_lookup
from app.models.annotation import AnnotationStatus, PoemAnnotation
from app.models.author import Author
from app.models.chunk import ChunkGranularity, ChunkStatus, PoemChunk
from app.models.dynasty import Dynasty
from app.models.poem import Poem, PoemStatus
from app.models.version import PoemVersion


@dataclass(frozen=True, slots=True)
class IndexableChunk:
    chunk_id: int
    vector_id: str | None
    content_hash: str
    poem_id: int
    poem_version_id: int
    annotation_id: int | None
    annotation_type: str | None
    granularity: str
    chunk_index: int
    text: str
    normalized_text: str
    line_start: int | None
    line_end: int | None
    chunk_strategy: str
    status: str
    title: str
    author_id: int | None
    author_name: str | None
    dynasty_id: int | None
    dynasty_name: str | None


@dataclass(frozen=True, slots=True)
class ChunkSearchCandidate:
    chunk_id: int
    vector_id: str | None
    poem_id: int
    poem_version_id: int
    annotation_id: int | None
    annotation_type: str | None
    granularity: str
    chunk_index: int
    text: str
    normalized_text: str
    line_start: int | None
    line_end: int | None
    chunk_strategy: str
    status: str
    title: str
    author_id: int | None
    author_name: str | None
    dynasty_id: int | None
    dynasty_name: str | None
    published_at: datetime | None


class ChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search_lexical(
        self,
        *,
        query: str,
        limit: int,
        granularities: Sequence[str] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
    ) -> list[ChunkSearchCandidate]:
        normalized_content_query = normalize_content(query)
        normalized_lookup_query = normalize_lookup(query)
        lexical_filters: list[ColumnElement[bool]] = []

        if normalized_content_query:
            content_pattern = _like_pattern(normalized_content_query)
            lexical_filters.append(
                func.lower(PoemChunk.normalized_text).like(content_pattern, escape="\\")
            )
        if normalized_lookup_query:
            metadata_pattern = _like_pattern(normalized_lookup_query)
            lexical_filters.extend(
                [
                    func.lower(Poem.title).like(metadata_pattern, escape="\\"),
                    func.lower(Author.name).like(metadata_pattern, escape="\\"),
                    func.lower(Dynasty.name).like(metadata_pattern, escape="\\"),
                ]
            )

        filters: list[ColumnElement[bool]] = [
            Poem.status == PoemStatus.PUBLISHED.value,
            Poem.deleted_at.is_(None),
            PoemVersion.version_no == Poem.version_no,
            PoemChunk.status.in_(
                [ChunkStatus.PENDING.value, ChunkStatus.READY.value]
            ),
            or_(
                PoemChunk.granularity != ChunkGranularity.NOTE.value,
                and_(
                    PoemChunk.annotation_id.is_not(None),
                    PoemAnnotation.status == AnnotationStatus.PUBLISHED.value,
                ),
            ),
        ]
        if granularities:
            filters.append(PoemChunk.granularity.in_(granularities))
        if author_id is not None:
            filters.append(Poem.author_id == author_id)
        if dynasty_id is not None:
            filters.append(Poem.dynasty_id == dynasty_id)
        if lexical_filters:
            filters.append(or_(*lexical_filters))

        statement = (
            select(
                PoemChunk.id,
                PoemChunk.vector_id,
                PoemChunk.poem_id,
                PoemChunk.poem_version_id,
                PoemChunk.annotation_id,
                PoemAnnotation.annotation_type,
                PoemChunk.granularity,
                PoemChunk.chunk_index,
                PoemChunk.text,
                PoemChunk.normalized_text,
                PoemChunk.line_start,
                PoemChunk.line_end,
                PoemChunk.chunk_strategy,
                PoemChunk.status,
                Poem.title,
                Poem.author_id,
                Author.name,
                Poem.dynasty_id,
                Dynasty.name,
                Poem.published_at,
            )
            .select_from(PoemChunk)
            .join(Poem, Poem.id == PoemChunk.poem_id)
            .join(PoemVersion, PoemVersion.id == PoemChunk.poem_version_id)
            .outerjoin(Author, Author.id == Poem.author_id)
            .outerjoin(Dynasty, Dynasty.id == Poem.dynasty_id)
            .outerjoin(PoemAnnotation, PoemAnnotation.id == PoemChunk.annotation_id)
            .where(*filters)
            .order_by(PoemChunk.id)
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            ChunkSearchCandidate(
                chunk_id=row[0],
                vector_id=row[1],
                poem_id=row[2],
                poem_version_id=row[3],
                annotation_id=row[4],
                annotation_type=row[5],
                granularity=row[6],
                chunk_index=row[7],
                text=row[8],
                normalized_text=row[9],
                line_start=row[10],
                line_end=row[11],
                chunk_strategy=row[12],
                status=row[13],
                title=row[14],
                author_id=row[15],
                author_name=row[16],
                dynasty_id=row[17],
                dynasty_name=row[18],
                published_at=row[19],
            )
            for row in rows
        ]

    async def list_public_by_vector_ids(
        self,
        *,
        vector_ids: Sequence[str],
        granularities: Sequence[str] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
        chunk_strategy: str | None = None,
    ) -> list[ChunkSearchCandidate]:
        if not vector_ids:
            return []

        filters: list[ColumnElement[bool]] = [
            PoemChunk.vector_id.in_(vector_ids),
            PoemChunk.status == ChunkStatus.READY.value,
            Poem.status == PoemStatus.PUBLISHED.value,
            Poem.deleted_at.is_(None),
            PoemVersion.version_no == Poem.version_no,
            or_(
                PoemChunk.granularity != ChunkGranularity.NOTE.value,
                and_(
                    PoemChunk.annotation_id.is_not(None),
                    PoemAnnotation.status == AnnotationStatus.PUBLISHED.value,
                ),
            ),
        ]
        if granularities:
            filters.append(PoemChunk.granularity.in_(granularities))
        if author_id is not None:
            filters.append(Poem.author_id == author_id)
        if dynasty_id is not None:
            filters.append(Poem.dynasty_id == dynasty_id)
        if chunk_strategy is not None:
            filters.append(PoemChunk.chunk_strategy == chunk_strategy)

        statement = (
            select(
                PoemChunk.id,
                PoemChunk.vector_id,
                PoemChunk.poem_id,
                PoemChunk.poem_version_id,
                PoemChunk.annotation_id,
                PoemAnnotation.annotation_type,
                PoemChunk.granularity,
                PoemChunk.chunk_index,
                PoemChunk.text,
                PoemChunk.normalized_text,
                PoemChunk.line_start,
                PoemChunk.line_end,
                PoemChunk.chunk_strategy,
                PoemChunk.status,
                Poem.title,
                Poem.author_id,
                Author.name,
                Poem.dynasty_id,
                Dynasty.name,
                Poem.published_at,
            )
            .select_from(PoemChunk)
            .join(Poem, Poem.id == PoemChunk.poem_id)
            .join(PoemVersion, PoemVersion.id == PoemChunk.poem_version_id)
            .outerjoin(Author, Author.id == Poem.author_id)
            .outerjoin(Dynasty, Dynasty.id == Poem.dynasty_id)
            .outerjoin(PoemAnnotation, PoemAnnotation.id == PoemChunk.annotation_id)
            .where(*filters)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            ChunkSearchCandidate(
                chunk_id=row[0],
                vector_id=row[1],
                poem_id=row[2],
                poem_version_id=row[3],
                annotation_id=row[4],
                annotation_type=row[5],
                granularity=row[6],
                chunk_index=row[7],
                text=row[8],
                normalized_text=row[9],
                line_start=row[10],
                line_end=row[11],
                chunk_strategy=row[12],
                status=row[13],
                title=row[14],
                author_id=row[15],
                author_name=row[16],
                dynasty_id=row[17],
                dynasty_name=row[18],
                published_at=row[19],
            )
            for row in rows
        ]

    async def list_indexable_by_version(
        self,
        version_id: int,
        *,
        chunk_strategy: str | None = None,
    ) -> list[IndexableChunk]:
        filters: list[ColumnElement[bool]] = [
            PoemChunk.poem_version_id == version_id,
            PoemChunk.status.in_(
                [
                    ChunkStatus.PENDING.value,
                    ChunkStatus.READY.value,
                ]
            ),
            or_(
                PoemChunk.granularity != ChunkGranularity.NOTE.value,
                and_(
                    PoemChunk.annotation_id.is_not(None),
                    PoemAnnotation.status == AnnotationStatus.PUBLISHED.value,
                ),
            ),
        ]
        if chunk_strategy is not None:
            filters.append(PoemChunk.chunk_strategy == chunk_strategy)

        statement = (
            select(
                PoemChunk.id,
                PoemChunk.vector_id,
                PoemChunk.content_hash,
                PoemChunk.poem_id,
                PoemChunk.poem_version_id,
                PoemChunk.annotation_id,
                PoemAnnotation.annotation_type,
                PoemChunk.granularity,
                PoemChunk.chunk_index,
                PoemChunk.text,
                PoemChunk.normalized_text,
                PoemChunk.line_start,
                PoemChunk.line_end,
                PoemChunk.chunk_strategy,
                PoemChunk.status,
                Poem.title,
                Poem.author_id,
                Author.name,
                Poem.dynasty_id,
                Dynasty.name,
            )
            .select_from(PoemChunk)
            .join(Poem, Poem.id == PoemChunk.poem_id)
            .join(PoemVersion, PoemVersion.id == PoemChunk.poem_version_id)
            .outerjoin(Author, Author.id == Poem.author_id)
            .outerjoin(Dynasty, Dynasty.id == Poem.dynasty_id)
            .outerjoin(PoemAnnotation, PoemAnnotation.id == PoemChunk.annotation_id)
            .where(*filters)
            .order_by(PoemChunk.granularity, PoemChunk.chunk_index, PoemChunk.id)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            IndexableChunk(
                chunk_id=row[0],
                vector_id=row[1],
                content_hash=row[2],
                poem_id=row[3],
                poem_version_id=row[4],
                annotation_id=row[5],
                annotation_type=row[6],
                granularity=row[7],
                chunk_index=row[8],
                text=row[9],
                normalized_text=row[10],
                line_start=row[11],
                line_end=row[12],
                chunk_strategy=row[13],
                status=row[14],
                title=row[15],
                author_id=row[16],
                author_name=row[17],
                dynasty_id=row[18],
                dynasty_name=row[19],
            )
            for row in rows
        ]

    async def mark_indexed(
        self,
        *,
        chunk_ids: Sequence[int],
        vector_ids: Sequence[str],
        embedding_model: str,
        embedding_dimension: int,
    ) -> None:
        if len(chunk_ids) != len(vector_ids):
            raise ValueError("chunk_ids and vector_ids must have the same length")
        if not chunk_ids:
            return
        await self.session.execute(
            update(PoemChunk)
            .where(PoemChunk.id.in_(chunk_ids))
            .values(
                vector_id=case(
                    {
                        chunk_id: vector_id
                        for chunk_id, vector_id in zip(chunk_ids, vector_ids, strict=True)
                    },
                    value=PoemChunk.id,
                ),
                embedding_model=embedding_model,
                embedding_dimension=embedding_dimension,
                status=ChunkStatus.READY.value,
            )
        )
        await self.session.flush()


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
