from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Row,
    Select,
    and_,
    case,
    func,
    literal,
    or_,
    select,
    union_all,
    update,
)
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


@dataclass(frozen=True, slots=True)
class LexicalSearchRequest:
    query: str
    limit: int
    granularities: tuple[str, ...] = ()
    author_id: int | None = None
    dynasty_id: int | None = None


def _candidate_statement() -> Select[Any]:
    """Select the public chunk columns shared by every candidate query."""

    return (
        select(
            PoemChunk.id.label("chunk_id"),
            PoemChunk.vector_id.label("vector_id"),
            PoemChunk.poem_id.label("poem_id"),
            PoemChunk.poem_version_id.label("poem_version_id"),
            PoemChunk.annotation_id.label("annotation_id"),
            PoemAnnotation.annotation_type.label("annotation_type"),
            PoemChunk.granularity.label("granularity"),
            PoemChunk.chunk_index.label("chunk_index"),
            PoemChunk.text.label("text"),
            PoemChunk.normalized_text.label("normalized_text"),
            PoemChunk.line_start.label("line_start"),
            PoemChunk.line_end.label("line_end"),
            PoemChunk.chunk_strategy.label("chunk_strategy"),
            PoemChunk.status.label("status"),
            Poem.title.label("title"),
            Poem.author_id.label("author_id"),
            Author.name.label("author_name"),
            Poem.dynasty_id.label("dynasty_id"),
            Dynasty.name.label("dynasty_name"),
            Poem.published_at.label("published_at"),
        )
        .select_from(PoemChunk)
        .join(Poem, Poem.id == PoemChunk.poem_id)
        .join(PoemVersion, PoemVersion.id == PoemChunk.poem_version_id)
        .outerjoin(Author, Author.id == Poem.author_id)
        .outerjoin(Dynasty, Dynasty.id == Poem.dynasty_id)
        .outerjoin(PoemAnnotation, PoemAnnotation.id == PoemChunk.annotation_id)
    )


def _candidate_from_row(row: Row[Any]) -> ChunkSearchCandidate:
    return ChunkSearchCandidate(
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


def _published_index_filter() -> ColumnElement[bool]:
    """Keep only the chunks published by the poem's active index run.

    ``poem.active_index_run_id`` is written in the same transaction that marks
    a run's chunks ready. Poems without a pointer predate that mechanism, so
    they keep the legacy version-based visibility; once a run is published,
    half-written chunks from a newer version or a newer run stay invisible.
    """

    return or_(
        Poem.active_index_run_id.is_(None),
        PoemChunk.index_run_id == Poem.active_index_run_id,
    )


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
        (
            normalized_content_query,
            normalized_lookup_query,
            filters,
        ) = _lexical_filters(
            query=query,
            granularities=granularities,
            author_id=author_id,
            dynasty_id=dynasty_id,
        )

        statement = (
            _candidate_statement()
            .where(*filters)
            .order_by(
                _lexical_priority(
                    normalized_content_query=normalized_content_query,
                    normalized_lookup_query=normalized_lookup_query,
                ),
                PoemChunk.id,
            )
            .limit(limit)
        )
        rows = (await self.session.execute(statement)).all()
        return [_candidate_from_row(row) for row in rows]

    async def search_lexical_batch(
        self,
        requests: Sequence[LexicalSearchRequest],
    ) -> list[list[ChunkSearchCandidate]]:
        """Run independent lexical searches in one database round trip."""

        if not requests:
            return []
        if any(request.limit < 1 for request in requests):
            raise ValueError("limit must be at least 1")

        branches: list[Select[Any]] = []
        for query_index, request in enumerate(requests):
            (
                normalized_content_query,
                normalized_lookup_query,
                filters,
            ) = _lexical_filters(
                query=request.query,
                granularities=request.granularities,
                author_id=request.author_id,
                dynasty_id=request.dynasty_id,
            )
            branches.append(
                _candidate_statement()
                .add_columns(
                    literal(query_index).label("query_index"),
                    _lexical_priority(
                        normalized_content_query=normalized_content_query,
                        normalized_lookup_query=normalized_lookup_query,
                    ).label("lexical_priority"),
                )
                .where(*filters)
            )

        unioned = union_all(*branches).subquery("lexical_candidates")
        ranked = select(
            *unioned.c,
            func.row_number()
            .over(
                partition_by=unioned.c.query_index,
                order_by=(unioned.c.lexical_priority, unioned.c.chunk_id),
            )
            .label("variant_rank"),
        ).subquery("ranked_lexical_candidates")
        variant_limits = case(
            {
                query_index: request.limit
                for query_index, request in enumerate(requests)
            },
            value=ranked.c.query_index,
            else_=0,
        )
        statement = (
            select(ranked)
            .where(ranked.c.variant_rank <= variant_limits)
            .order_by(ranked.c.query_index, ranked.c.variant_rank)
        )
        rows = (await self.session.execute(statement)).all()

        results: list[list[ChunkSearchCandidate]] = [
            [] for _ in requests
        ]
        for row in rows:
            query_index = int(row._mapping["query_index"])
            results[query_index].append(_candidate_from_row(row))
        return results

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
            _published_index_filter(),
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

        statement = _candidate_statement().where(*filters)
        rows = (await self.session.execute(statement)).all()
        return [_candidate_from_row(row) for row in rows]

    async def list_poem_chunks_by_version_ids(
        self,
        *,
        version_ids: Sequence[int],
    ) -> list[ChunkSearchCandidate]:
        """Load the poem-level chunks of the given versions, in reading order."""

        if not version_ids:
            return []

        statement = (
            _candidate_statement()
            .where(
                PoemChunk.poem_version_id.in_(version_ids),
                PoemChunk.granularity == ChunkGranularity.POEM.value,
                PoemChunk.status.in_(
                    [ChunkStatus.PENDING.value, ChunkStatus.READY.value]
                ),
                Poem.status == PoemStatus.PUBLISHED.value,
                Poem.deleted_at.is_(None),
                PoemVersion.version_no == Poem.version_no,
                _published_index_filter(),
            )
            .order_by(
                PoemChunk.poem_version_id,
                PoemChunk.chunk_index,
                PoemChunk.id,
            )
        )
        rows = (await self.session.execute(statement)).all()
        return [_candidate_from_row(row) for row in rows]

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
        index_run_id: int,
        poem_version_id: int,
    ) -> int:
        """Mark chunks ready and publish the poem's active index pointer.

        Both writes happen in the caller's transaction, so retrieval never
        observes a run whose chunks are partially written. The pointer only
        moves when the run still indexes the poem's active version: a run that
        finishes after the poem moved on to a newer version must not hide that
        newer version.

        Returns the number of poems whose pointer moved to ``index_run_id``.
        """

        if len(chunk_ids) != len(vector_ids):
            raise ValueError("chunk_ids and vector_ids must have the same length")
        if not chunk_ids:
            return 0
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
                index_run_id=index_run_id,
                status=ChunkStatus.READY.value,
            )
        )
        active_version_no = (
            select(PoemVersion.version_no)
            .where(PoemVersion.id == poem_version_id)
            .scalar_subquery()
        )
        poem_id = (
            select(PoemChunk.poem_id)
            .where(PoemChunk.id == chunk_ids[0])
            .scalar_subquery()
        )
        result = await self.session.execute(
            update(Poem)
            .where(
                Poem.id == poem_id,
                Poem.version_no == active_version_no,
            )
            .values(active_index_run_id=index_run_id)
        )
        await self.session.flush()
        return int(result.rowcount or 0)


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _lexical_filters(
    *,
    query: str,
    granularities: Sequence[str] | None,
    author_id: int | None,
    dynasty_id: int | None,
) -> tuple[str, str, list[ColumnElement[bool]]]:
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
        _published_index_filter(),
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

    return normalized_content_query, normalized_lookup_query, filters


def _lexical_priority(
    *,
    normalized_content_query: str,
    normalized_lookup_query: str,
) -> ColumnElement[int]:
    """Order broad LIKE candidates before applying the SQL limit.

    A short query such as "登高" can appear in thousands of poem bodies. The
    previous query ordered by chunk id, so metadata matches could be truncated
    before the service-level scorer saw them. These priorities keep exact
    content and metadata hits in the candidate pool without hard-coding any
    poem title or author.
    """

    whens: list[tuple[ColumnElement[bool], int]] = []
    if normalized_content_query:
        whens.extend(
            [
                (
                    func.lower(PoemChunk.normalized_text)
                    == normalized_content_query,
                    0,
                ),
            ]
        )
    if normalized_lookup_query:
        metadata_pattern = _like_pattern(normalized_lookup_query)
        whens.extend(
            [
                (func.lower(Poem.title) == normalized_lookup_query, 1),
                (func.lower(Author.name) == normalized_lookup_query, 2),
                (func.lower(Dynasty.name) == normalized_lookup_query, 3),
                (
                    func.lower(Poem.title).like(
                        metadata_pattern,
                        escape="\\",
                    ),
                    4,
                ),
                (
                    func.lower(Author.name).like(
                        metadata_pattern,
                        escape="\\",
                    ),
                    5,
                ),
                (
                    func.lower(Dynasty.name).like(
                        metadata_pattern,
                        escape="\\",
                    ),
                    6,
                ),
            ]
        )
    if normalized_content_query:
        whens.append(
            (
                func.lower(PoemChunk.normalized_text).like(
                    _like_pattern(normalized_content_query),
                    escape="\\",
                ),
                7,
            )
        )
    if not whens:
        return case(else_=99)
    return case(*whens, else_=99)
