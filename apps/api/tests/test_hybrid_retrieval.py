from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from app.core.errors import AppError, ErrorCode
from app.models.chunk import ChunkGranularity
from app.schemas.retrieval import RetrievalEvidence
from app.services.hybrid_retrieval import HybridRetrievalService
from app.services.retrieval import RetrievalSearchResult


@dataclass
class FakeBranch:
    items: list[RetrievalEvidence]
    strategy: str = "fake-branch"
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
        granularities: list[ChunkGranularity] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
    ) -> RetrievalSearchResult:
        self.calls.append(
            {
                "query": query,
                "limit": limit,
                "granularities": granularities,
                "author_id": author_id,
                "dynasty_id": dynasty_id,
            }
        )
        if self.error is not None:
            raise self.error
        return RetrievalSearchResult(
            items=self.items,
            strategy=self.strategy,
            normalized_query=query.strip(),
            candidate_count=len(self.items),
        )


def _evidence(
    *,
    chunk_id: int,
    score: float,
    match_types: list[str],
) -> RetrievalEvidence:
    return RetrievalEvidence(
        chunk_id=chunk_id,
        poem_id=1,
        poem_version_id=1,
        annotation_id=None,
        annotation_type=None,
        title=f"Poem {chunk_id}",
        author_id=1,
        author_name="李白",
        dynasty_id=1,
        dynasty_name="唐",
        granularity=ChunkGranularity.LINE,
        chunk_index=chunk_id,
        text=f"Line {chunk_id}",
        line_start=chunk_id,
        line_end=chunk_id,
        chunk_strategy="structural-v1",
        status="ready",
        score=score,
        match_types=match_types,
        published_at=None,
    )


@pytest.mark.asyncio
async def test_hybrid_merges_sources_with_rrf_and_preserves_match_types() -> None:
    lexical = FakeBranch(
        items=[
            _evidence(chunk_id=1, score=0.9, match_types=["chunk_exact"]),
            _evidence(chunk_id=2, score=0.7, match_types=["chunk_phrase"]),
        ]
    )
    dense = FakeBranch(
        items=[
            _evidence(chunk_id=2, score=0.95, match_types=["dense_similarity"]),
            _evidence(chunk_id=3, score=0.8, match_types=["dense_similarity"]),
        ]
    )

    result = await HybridRetrievalService(lexical, dense).search_evidence(
        query="明月",
        limit=3,
    )

    assert result.strategy == "hybrid-rrf-v1"
    assert result.candidate_count == 3
    assert [item.chunk_id for item in result.items] == [2, 1, 3]
    assert [item.score for item in result.items] == [0.991935, 0.5, 0.491935]
    assert result.items[0].match_types == [
        "lexical_match",
        "chunk_phrase",
        "dense_similarity",
        "rrf_fusion",
    ]
    assert result.items[1].match_types == [
        "lexical_match",
        "chunk_exact",
        "rrf_fusion",
    ]
    assert result.items[2].match_types == ["dense_similarity", "rrf_fusion"]
    assert lexical.calls[0]["limit"] == 15
    assert dense.calls[0]["limit"] == 15


@pytest.mark.asyncio
async def test_hybrid_returns_single_source_hits_and_forwards_filters() -> None:
    lexical = FakeBranch(
        items=[_evidence(chunk_id=4, score=0.8, match_types=["chunk_phrase"])]
    )
    dense = FakeBranch(items=[])

    result = await HybridRetrievalService(lexical, dense).search_evidence(
        query="赏析",
        limit=5,
        granularities=[ChunkGranularity.NOTE],
        author_id=7,
        dynasty_id=8,
    )

    assert len(result.items) == 1
    assert result.items[0].chunk_id == 4
    assert result.items[0].score == 0.5
    assert lexical.calls[0]["granularities"] == [ChunkGranularity.NOTE]
    assert lexical.calls[0]["author_id"] == 7
    assert lexical.calls[0]["dynasty_id"] == 8
    assert dense.calls[0]["granularities"] == [ChunkGranularity.NOTE]
    assert dense.calls[0]["author_id"] == 7
    assert dense.calls[0]["dynasty_id"] == 8


@pytest.mark.asyncio
async def test_hybrid_uses_chunk_id_as_stable_tie_breaker() -> None:
    lexical = FakeBranch(
        items=[_evidence(chunk_id=9, score=0.9, match_types=["chunk_exact"])]
    )
    dense = FakeBranch(
        items=[_evidence(chunk_id=5, score=0.9, match_types=["dense_similarity"])]
    )

    result = await HybridRetrievalService(lexical, dense).search_evidence(
        query="tie",
        limit=2,
    )

    assert [item.chunk_id for item in result.items] == [5, 9]
    assert [item.score for item in result.items] == [0.5, 0.5]


@pytest.mark.asyncio
async def test_hybrid_rejects_blank_query_without_calling_branches() -> None:
    lexical = FakeBranch(items=[])
    dense = FakeBranch(items=[])

    with pytest.raises(AppError) as error:
        await HybridRetrievalService(lexical, dense).search_evidence(
            query="   ",
            limit=5,
        )

    assert error.value.code == ErrorCode.VALIDATION_ERROR
    assert lexical.calls == []
    assert dense.calls == []


@pytest.mark.asyncio
async def test_hybrid_fails_closed_when_either_branch_fails() -> None:
    lexical = FakeBranch(items=[])
    dense = FakeBranch(
        items=[],
        error=AppError(
            status_code=503,
            code=ErrorCode.VECTOR_STORE_ERROR,
            message="vector store unavailable",
        ),
    )

    with pytest.raises(AppError) as error:
        await HybridRetrievalService(lexical, dense).search_evidence(
            query="明月",
            limit=5,
        )

    assert error.value.code == ErrorCode.VECTOR_STORE_ERROR
    assert len(lexical.calls) == 1
    assert len(dense.calls) == 1
