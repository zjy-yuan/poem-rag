from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest
from app.models.chunk import ChunkGranularity
from app.repositories.chunks import ChunkSearchCandidate
from app.schemas.retrieval import RetrievalEvidence
from app.services.evidence_context import (
    PARENT_CONTEXT_MATCH,
    PoemContextRetrievalService,
)
from app.services.retrieval import RetrievalSearchResult


@dataclass
class FakeRetriever:
    result: RetrievalSearchResult
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
        return self.result


@dataclass
class FakeContextSource:
    candidates: list[ChunkSearchCandidate]
    calls: list[list[int]] = field(default_factory=list)

    async def list_poem_chunks_by_version_ids(
        self,
        *,
        version_ids: Sequence[int],
    ) -> list[ChunkSearchCandidate]:
        requested = list(version_ids)
        self.calls.append(requested)
        return [
            candidate
            for candidate in self.candidates
            if candidate.poem_version_id in requested
        ]


def _evidence(
    *,
    chunk_id: int,
    poem_version_id: int,
    granularity: ChunkGranularity = ChunkGranularity.LINE,
    score: float = 0.5,
) -> RetrievalEvidence:
    return RetrievalEvidence(
        chunk_id=chunk_id,
        poem_id=poem_version_id,
        poem_version_id=poem_version_id,
        annotation_id=None,
        annotation_type=None,
        title=f"Poem {poem_version_id}",
        author_id=1,
        author_name="苏轼",
        dynasty_id=1,
        dynasty_name="宋",
        granularity=granularity,
        chunk_index=chunk_id,
        text=f"Chunk {chunk_id}",
        line_start=1,
        line_end=1,
        chunk_strategy="structural-v1",
        status="ready",
        score=score,
        match_types=["title_phrase"],
        published_at=None,
    )


def _candidate(
    *,
    chunk_id: int,
    poem_version_id: int,
    chunk_index: int = 0,
) -> ChunkSearchCandidate:
    return ChunkSearchCandidate(
        chunk_id=chunk_id,
        vector_id=f"vector-{chunk_id}",
        poem_id=poem_version_id,
        poem_version_id=poem_version_id,
        annotation_id=None,
        annotation_type=None,
        granularity=ChunkGranularity.POEM.value,
        chunk_index=chunk_index,
        text=f"Chunk {chunk_id}",
        normalized_text=f"chunk{chunk_id}",
        line_start=1,
        line_end=2,
        chunk_strategy="structural-v1",
        status="ready",
        title=f"Poem {poem_version_id}",
        author_id=1,
        author_name="苏轼",
        dynasty_id=1,
        dynasty_name="宋",
        published_at=None,
    )


def _result(
    *items: RetrievalEvidence,
    candidate_count: int = 9,
) -> RetrievalSearchResult:
    return RetrievalSearchResult(
        items=list(items),
        strategy="expanded-lexical-v1",
        normalized_query="水调歌头明月几时有",
        candidate_count=candidate_count,
    )


@pytest.mark.asyncio
async def test_line_matches_are_expanded_with_the_poem_context() -> None:
    retriever = FakeRetriever(
        _result(
            _evidence(chunk_id=1, poem_version_id=7, score=0.5),
            _evidence(chunk_id=2, poem_version_id=7, score=0.48),
        )
    )
    source = FakeContextSource([_candidate(chunk_id=9, poem_version_id=7)])

    result = await PoemContextRetrievalService(
        retriever,
        source,
    ).search_evidence(query="水调歌头", limit=5)

    assert [item.chunk_id for item in result.items] == [1, 2, 9]
    context = result.items[-1]
    assert context.granularity == ChunkGranularity.POEM
    assert context.match_types == [PARENT_CONTEXT_MATCH]
    assert context.score == 0.5
    assert result.strategy == "expanded-lexical-v1"
    assert result.candidate_count == 9
    assert source.calls == [[7]]
    assert retriever.calls == [
        {
            "query": "水调歌头",
            "limit": 5,
            "granularities": None,
            "author_id": None,
            "dynasty_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_versions_with_a_selected_poem_chunk_are_not_expanded() -> None:
    retriever = FakeRetriever(
        _result(
            _evidence(
                chunk_id=1,
                poem_version_id=7,
                granularity=ChunkGranularity.POEM,
            ),
            _evidence(chunk_id=2, poem_version_id=7),
        )
    )
    source = FakeContextSource([_candidate(chunk_id=9, poem_version_id=7)])

    result = await PoemContextRetrievalService(
        retriever,
        source,
    ).search_evidence(query="水调歌头", limit=5)

    assert [item.chunk_id for item in result.items] == [1, 2]
    assert source.calls == []


@pytest.mark.asyncio
async def test_expansion_prefers_the_best_ranked_poem_and_respects_the_budget() -> None:
    retriever = FakeRetriever(
        _result(
            _evidence(chunk_id=1, poem_version_id=7, score=0.5),
            _evidence(chunk_id=2, poem_version_id=8, score=0.4),
        )
    )
    source = FakeContextSource(
        [
            _candidate(chunk_id=9, poem_version_id=7),
            _candidate(chunk_id=10, poem_version_id=8),
        ]
    )

    result = await PoemContextRetrievalService(
        retriever,
        source,
        max_context_chunks=1,
    ).search_evidence(query="明月", limit=5)

    assert [item.chunk_id for item in result.items] == [1, 2, 9]
    assert result.items[-1].score == 0.5
    assert source.calls == [[7, 8]]


@pytest.mark.asyncio
async def test_expansion_is_skipped_when_no_context_is_available() -> None:
    retriever = FakeRetriever(_result(_evidence(chunk_id=1, poem_version_id=7)))
    source = FakeContextSource([])

    result = await PoemContextRetrievalService(
        retriever,
        source,
    ).search_evidence(query="明月", limit=5)

    assert [item.chunk_id for item in result.items] == [1]


def test_negative_context_budget_is_rejected() -> None:
    with pytest.raises(ValueError):
        PoemContextRetrievalService(
            FakeRetriever(_result()),
            FakeContextSource([]),
            max_context_chunks=-1,
        )
