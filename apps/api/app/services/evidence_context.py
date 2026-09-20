from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.models.chunk import ChunkGranularity
from app.repositories.chunks import ChunkSearchCandidate
from app.services.retrieval import (
    EvidenceRetriever,
    RetrievalSearchResult,
    to_retrieval_evidence,
)

PARENT_CONTEXT_MATCH = "parent_context"
DEFAULT_MAX_CONTEXT_CHUNKS = 3


class PoemContextSource(Protocol):
    async def list_poem_chunks_by_version_ids(
        self,
        *,
        version_ids: Sequence[int],
    ) -> list[ChunkSearchCandidate]: ...


class PoemContextRetrievalService:
    """Append poem-level context to a ranked evidence list.

    Ranking rewards chunks that match the query closely, which in poetry means
    individual lines. Assessment and generation still need the whole poem to
    read imagery and emotion, so every matched version without a poem-level
    chunk gets its context appended after the ranked evidence. The appended
    chunks keep their own rank, so citations stay traceable.
    """

    def __init__(
        self,
        retrieval: EvidenceRetriever,
        context_source: PoemContextSource,
        *,
        max_context_chunks: int = DEFAULT_MAX_CONTEXT_CHUNKS,
    ) -> None:
        if max_context_chunks < 0:
            raise ValueError("max_context_chunks must not be negative")
        self.retrieval = retrieval
        self.context_source = context_source
        self.max_context_chunks = max_context_chunks

    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
        granularities: list[ChunkGranularity] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
    ) -> RetrievalSearchResult:
        result = await self.retrieval.search_evidence(
            query=query,
            limit=limit,
            granularities=granularities,
            author_id=author_id,
            dynasty_id=dynasty_id,
        )
        version_ids = _versions_missing_poem_chunk(result)
        if not version_ids or self.max_context_chunks == 0:
            return result

        candidates = await self.context_source.list_poem_chunks_by_version_ids(
            version_ids=version_ids,
        )
        selected_ids = {item.chunk_id for item in result.items}
        best_score_by_version = _best_score_by_version(result)
        context_items = [
            to_retrieval_evidence(
                candidate,
                score=best_score_by_version.get(candidate.poem_version_id, 0.0),
                match_types=[PARENT_CONTEXT_MATCH],
            )
            for candidate in candidates
            if candidate.chunk_id not in selected_ids
        ][: self.max_context_chunks]
        if not context_items:
            return result

        return RetrievalSearchResult(
            items=[*result.items, *context_items],
            strategy=result.strategy,
            normalized_query=result.normalized_query,
            candidate_count=result.candidate_count,
        )


def _versions_missing_poem_chunk(
    result: RetrievalSearchResult,
) -> list[int]:
    """Version ids of the matched poems, best-ranked poem first."""

    versions_with_poem_chunk = {
        item.poem_version_id
        for item in result.items
        if item.granularity == ChunkGranularity.POEM
    }
    version_ids: list[int] = []
    for item in result.items:
        if item.poem_version_id in versions_with_poem_chunk:
            continue
        if item.poem_version_id not in version_ids:
            version_ids.append(item.poem_version_id)
    return version_ids


def _best_score_by_version(
    result: RetrievalSearchResult,
) -> dict[int, float]:
    scores: dict[int, float] = {}
    for item in result.items:
        current = scores.get(item.poem_version_id)
        if current is None or item.score > current:
            scores[item.poem_version_id] = item.score
    return scores
