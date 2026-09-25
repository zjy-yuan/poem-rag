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
DEFAULT_MAX_CONTEXT_CHUNKS = 40
DEFAULT_MAX_CONTEXT_CHARS = 4800


class PoemContextSource(Protocol):
    async def list_poem_chunks_by_version_ids(
        self,
        *,
        version_ids: Sequence[int],
    ) -> list[ChunkSearchCandidate]: ...


class PoemContextRetrievalService:
    """Complete poem-level context for the ranked evidence list.

    Ranking rewards chunks that match the query closely, which in poetry means
    individual lines. Assessment and generation still need the whole poem to
    read imagery and emotion, so missing poem-level chunks are appended after
    the ranked evidence. Long poems are represented by many short structural
    chunks, so selection rotates across matched versions under a character
    budget instead of keeping only the first few chunks of one poem.
    """

    def __init__(
        self,
        retrieval: EvidenceRetriever,
        context_source: PoemContextSource,
        *,
        max_context_chunks: int = DEFAULT_MAX_CONTEXT_CHUNKS,
        max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
    ) -> None:
        if max_context_chunks < 0:
            raise ValueError("max_context_chunks must not be negative")
        if max_context_chars < 0:
            raise ValueError("max_context_chars must not be negative")
        self.retrieval = retrieval
        self.context_source = context_source
        self.max_context_chunks = max_context_chunks
        self.max_context_chars = max_context_chars

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
        version_ids = _versions_in_rank_order(result)
        if (
            not version_ids
            or self.max_context_chunks == 0
            or self.max_context_chars == 0
        ):
            return result

        candidates = await self.context_source.list_poem_chunks_by_version_ids(
            version_ids=version_ids,
        )
        selected_ids = {item.chunk_id for item in result.items}
        candidates_by_version = _group_context_candidates(
            candidates,
            version_ids=version_ids,
            excluded_chunk_ids=selected_ids,
        )
        selected_candidates = _select_context_candidates(
            candidates_by_version,
            version_ids=version_ids,
            max_chunks=self.max_context_chunks,
            max_chars=self.max_context_chars,
        )
        if not selected_candidates:
            return result

        best_score_by_version = _best_score_by_version(result)
        context_items = [
            to_retrieval_evidence(
                candidate,
                score=best_score_by_version.get(candidate.poem_version_id, 0.0),
                match_types=[PARENT_CONTEXT_MATCH],
            )
            for candidate in selected_candidates
        ]

        return RetrievalSearchResult(
            items=[*result.items, *context_items],
            strategy=result.strategy,
            normalized_query=result.normalized_query,
            candidate_count=result.candidate_count,
            hard_filtered=result.hard_filtered,
        )


def _versions_in_rank_order(
    result: RetrievalSearchResult,
) -> list[int]:
    """Version ids of the matched poems, best-ranked poem first."""

    version_ids: list[int] = []
    for item in result.items:
        if item.poem_version_id not in version_ids:
            version_ids.append(item.poem_version_id)
    return version_ids


def _group_context_candidates(
    candidates: list[ChunkSearchCandidate],
    *,
    version_ids: list[int],
    excluded_chunk_ids: set[int],
) -> dict[int, list[ChunkSearchCandidate]]:
    grouped = {version_id: [] for version_id in version_ids}
    for candidate in candidates:
        if candidate.chunk_id in excluded_chunk_ids:
            continue
        if candidate.poem_version_id not in grouped:
            continue
        grouped[candidate.poem_version_id].append(candidate)

    for items in grouped.values():
        items.sort(key=lambda item: (item.chunk_index, item.chunk_id))
    return grouped


def _select_context_candidates(
    candidates_by_version: dict[int, list[ChunkSearchCandidate]],
    *,
    version_ids: list[int],
    max_chunks: int,
    max_chars: int,
) -> list[ChunkSearchCandidate]:
    """Select missing chunks fairly across matched versions."""

    selected: list[ChunkSearchCandidate] = []
    positions = {version_id: 0 for version_id in version_ids}
    used_chars = 0

    while len(selected) < max_chunks and used_chars < max_chars:
        added = False
        for version_id in version_ids:
            items = candidates_by_version[version_id]
            position = positions[version_id]
            if position >= len(items):
                continue

            candidate = items[position]
            positions[version_id] += 1
            if used_chars + len(candidate.text) > max_chars:
                continue

            selected.append(candidate)
            used_chars += len(candidate.text)
            added = True
            if len(selected) >= max_chunks or used_chars >= max_chars:
                break

        if not added:
            break

    return selected


def _best_score_by_version(
    result: RetrievalSearchResult,
) -> dict[int, float]:
    scores: dict[int, float] = {}
    for item in result.items:
        current = scores.get(item.poem_version_id)
        if current is None or item.score > current:
            scores[item.poem_version_id] = item.score
    return scores
