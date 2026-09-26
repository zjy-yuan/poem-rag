from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.text import normalize_content, normalize_lookup
from app.models.chunk import ChunkGranularity
from app.repositories.chunks import ChunkRepository, ChunkSearchCandidate
from app.schemas.retrieval import RetrievalEvidence

RETRIEVAL_STRATEGY = "lexical-baseline-v1"

_MATCH_CHUNK_EXACT = "chunk_exact"
_MATCH_CHUNK_PHRASE = "chunk_phrase"
_MATCH_TITLE_PHRASE = "title_phrase"
_MATCH_AUTHOR_PHRASE = "author_phrase"
_MATCH_DYNASTY_PHRASE = "dynasty_phrase"

_GRANULARITY_PRIORITY = {
    ChunkGranularity.LINE.value: 0,
    ChunkGranularity.POEM.value: 1,
    ChunkGranularity.NOTE.value: 2,
}


@dataclass(frozen=True, slots=True)
class RetrievalSearchResult:
    items: list[RetrievalEvidence]
    strategy: str
    normalized_query: str
    candidate_count: int
    hard_filtered: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    query: str
    limit: int
    granularities: tuple[ChunkGranularity, ...] = ()
    author_id: int | None = None
    dynasty_id: int | None = None


class EvidenceRetriever(Protocol):
    """Any retrieval stage that returns ranked evidence for one query."""

    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
        granularities: list[ChunkGranularity] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
    ) -> RetrievalSearchResult: ...


@runtime_checkable
class BatchEvidenceRetriever(Protocol):
    """Optional retrieval capability for batching independent query branches."""

    async def search_evidence_batch(
        self,
        requests: Sequence[RetrievalRequest],
    ) -> list[RetrievalSearchResult]: ...


class RetrievalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.chunks = ChunkRepository(session)

    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
        granularities: list[ChunkGranularity] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
    ) -> RetrievalSearchResult:
        normalized_content_query = normalize_content(query)
        normalized_lookup_query = normalize_lookup(query)
        if not normalized_content_query and not normalized_lookup_query:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="查询内容不能为空",
            )

        candidates = await self.chunks.search_lexical(
            query=query,
            limit=min(max(limit * 5, limit), 200),
            granularities=(
                [granularity.value for granularity in granularities]
                if granularities
                else None
            ),
            author_id=author_id,
            dynasty_id=dynasty_id,
        )
        scored = [
            (candidate, *self._score(candidate, normalized_content_query, normalized_lookup_query))
            for candidate in candidates
        ]
        scored = [item for item in scored if item[1] > 0]
        scored.sort(
            key=lambda item: (
                -item[1],
                _GRANULARITY_PRIORITY.get(item[0].granularity, 99),
                item[0].chunk_id,
            )
        )
        selected = scored[:limit]

        return RetrievalSearchResult(
            items=[
                to_retrieval_evidence(
                    candidate,
                    score=score,
                    match_types=match_types,
                )
                for candidate, score, match_types in selected
            ],
            strategy=RETRIEVAL_STRATEGY,
            normalized_query=normalized_content_query or normalized_lookup_query,
            candidate_count=len(scored),
        )

    @staticmethod
    def _score(
        candidate: ChunkSearchCandidate,
        normalized_content_query: str,
        normalized_lookup_query: str,
    ) -> tuple[float, list[str]]:
        score = 0.0
        match_types: list[str] = []

        if normalized_content_query and candidate.normalized_text == normalized_content_query:
            score = 1.0
            match_types.append(_MATCH_CHUNK_EXACT)
        elif normalized_content_query and normalized_content_query in candidate.normalized_text:
            ratio = len(normalized_content_query) / max(len(candidate.normalized_text), 1)
            score = max(score, 0.7 + (0.25 * ratio))
            match_types.append(_MATCH_CHUNK_PHRASE)

        if normalized_lookup_query:
            normalized_title = normalize_lookup(candidate.title)
            if normalized_title == normalized_lookup_query:
                score = max(score, 1.0)
                match_types.append(_MATCH_TITLE_PHRASE)
            elif normalized_lookup_query in normalized_title:
                score = max(score, 0.8)
                match_types.append(_MATCH_TITLE_PHRASE)

            if candidate.author_name:
                normalized_author = normalize_lookup(candidate.author_name)
                if normalized_author == normalized_lookup_query:
                    score = max(score, 0.95)
                    match_types.append(_MATCH_AUTHOR_PHRASE)
                elif normalized_lookup_query in normalized_author:
                    score = max(score, 0.6)
                    match_types.append(_MATCH_AUTHOR_PHRASE)

            if candidate.dynasty_name:
                normalized_dynasty = normalize_lookup(candidate.dynasty_name)
                if normalized_dynasty == normalized_lookup_query:
                    score = max(score, 0.9)
                    match_types.append(_MATCH_DYNASTY_PHRASE)
                elif normalized_lookup_query in normalized_dynasty:
                    score = max(score, 0.55)
                    match_types.append(_MATCH_DYNASTY_PHRASE)

        return round(min(score, 1.0), 6), match_types



def to_retrieval_evidence(
    candidate: ChunkSearchCandidate,
    *,
    score: float,
    match_types: list[str],
) -> RetrievalEvidence:
    """Map a stored chunk onto the public evidence shape."""

    return RetrievalEvidence(
        chunk_id=candidate.chunk_id,
        poem_id=candidate.poem_id,
        poem_version_id=candidate.poem_version_id,
        annotation_id=candidate.annotation_id,
        annotation_type=candidate.annotation_type,
        title=candidate.title,
        author_id=candidate.author_id,
        author_name=candidate.author_name,
        dynasty_id=candidate.dynasty_id,
        dynasty_name=candidate.dynasty_name,
        granularity=ChunkGranularity(candidate.granularity),
        chunk_index=candidate.chunk_index,
        text=candidate.text,
        line_start=candidate.line_start,
        line_end=candidate.line_end,
        chunk_strategy=candidate.chunk_strategy,
        status=candidate.status,
        score=score,
        match_types=match_types,
        published_at=candidate.published_at,
    )
