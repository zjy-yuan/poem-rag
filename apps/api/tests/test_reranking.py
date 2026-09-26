from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from app.models.chunk import ChunkGranularity
from app.schemas.retrieval import RetrievalEvidence
from app.services.reranking import (
    DeterministicEvidenceReranker,
    RerankedRetrievalService,
)
from app.services.retrieval import RetrievalSearchResult


def _evidence(
    *,
    chunk_id: int,
    title: str,
    text: str,
    score: float,
    poem_id: int | None = None,
    author_name: str | None = "李白",
    dynasty_name: str | None = "唐",
) -> RetrievalEvidence:
    return RetrievalEvidence(
        chunk_id=chunk_id,
        poem_id=poem_id if poem_id is not None else chunk_id,
        poem_version_id=1,
        annotation_id=None,
        annotation_type=None,
        title=title,
        author_id=1,
        author_name=author_name,
        dynasty_id=1,
        dynasty_name=dynasty_name,
        granularity=ChunkGranularity.LINE,
        chunk_index=chunk_id,
        text=text,
        line_start=chunk_id,
        line_end=chunk_id,
        chunk_strategy="structural-v1",
        status="ready",
        score=score,
        match_types=["rrf_fusion"],
        published_at=None,
    )


@dataclass
class FakeRetrieval:
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
class FakeReranker:
    items: list[RetrievalEvidence] | None = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def rerank(
        self,
        *,
        query: str,
        evidence: list[RetrievalEvidence],
        limit: int,
    ) -> list[RetrievalEvidence]:
        self.calls.append(
            {
                "query": query,
                "evidence": evidence,
                "limit": limit,
            }
        )
        if self.error is not None:
            raise self.error
        if self.items is not None:
            return self.items
        return evidence[:limit]


@pytest.mark.asyncio
async def test_deterministic_reranker_prioritizes_exact_text_match() -> None:
    reranker = DeterministicEvidenceReranker()
    evidence = [
        _evidence(
            chunk_id=1,
            title="春晓",
            text="春眠不觉晓，处处闻啼鸟。",
            score=1.0,
        ),
        _evidence(
            chunk_id=2,
            title="静夜思",
            text="举头望明月，低头思故乡。",
            score=0.1,
        ),
        _evidence(
            chunk_id=3,
            title="静夜思",
            text="床前明月光，疑是地上霜。",
            score=1.0,
        ),
    ]

    reranked = await reranker.rerank(
        query="举头望明月，低头思故乡。",
        evidence=evidence,
        limit=2,
    )

    assert [item.chunk_id for item in reranked] == [2, 3]
    assert "deterministic_rerank" in reranked[0].match_types
    assert reranked[0].score > reranked[1].score


@pytest.mark.asyncio
async def test_deterministic_reranker_uses_title_and_author_metadata() -> None:
    reranker = DeterministicEvidenceReranker()
    evidence = [
        _evidence(
            chunk_id=1,
            title="归园田居",
            text="采菊东篱下，悠然见南山。",
            score=1.0,
            author_name="陶渊明",
            dynasty_name="魏晋",
        ),
        _evidence(
            chunk_id=2,
            title="饮酒·其五",
            text="结庐在人境，而无车马喧。",
            score=0.2,
            author_name="陶渊明",
            dynasty_name="魏晋",
        ),
        _evidence(
            chunk_id=3,
            title="竹石",
            text="采菊东篱下，悠然见南山。",
            score=1.0,
            author_name="郑燮",
            dynasty_name="清",
        ),
    ]

    reranked = await reranker.rerank(
        query="陶渊明《饮酒·其五》怎样写采菊",
        evidence=evidence,
        limit=2,
    )

    assert reranked[0].chunk_id == 2


@pytest.mark.asyncio
async def test_deterministic_reranker_limits_candidates_per_poem() -> None:
    reranker = DeterministicEvidenceReranker(max_per_poem=2)
    evidence = [
        _evidence(
            chunk_id=index,
            title="静夜思",
            text="举头望明月，低头思故乡。",
            score=1.0,
            poem_id=1,
        )
        for index in range(1, 4)
    ]
    evidence.append(
        _evidence(
            chunk_id=4,
            title="春晓",
            text="春眠不觉晓，处处闻啼鸟。",
            score=0.5,
            poem_id=2,
        )
    )

    reranked = await reranker.rerank(
        query="举头望明月，低头思故乡。",
        evidence=evidence,
        limit=3,
    )

    assert [item.chunk_id for item in reranked] == [1, 2, 4]


@pytest.mark.asyncio
async def test_reranked_service_expands_candidates_and_forwards_filters() -> None:
    upstream_items = [
        _evidence(
            chunk_id=1,
            title="静夜思",
            text="举头望明月，低头思故乡。",
            score=0.9,
        )
    ]
    retrieval = FakeRetrieval(
        RetrievalSearchResult(
            items=upstream_items,
            strategy="expanded-hybrid-rrf-v1",
            normalized_query="明月",
            candidate_count=17,
        )
    )
    reranker = FakeReranker()
    service = RerankedRetrievalService(
        retrieval,
        reranker,
        candidate_limit=30,
    )

    result = await service.search_evidence(
        query="明月",
        limit=5,
        granularities=[ChunkGranularity.LINE],
        author_id=7,
        dynasty_id=8,
    )

    assert result.items == upstream_items
    assert result.strategy == "expanded-hybrid-rerank-v1"
    assert result.normalized_query == "明月"
    assert result.candidate_count == 17
    assert result.hard_filtered is False
    assert retrieval.calls == [
        {
            "query": "明月",
            "limit": 30,
            "granularities": [ChunkGranularity.LINE],
            "author_id": 7,
            "dynasty_id": 8,
        }
    ]
    assert reranker.calls[0]["limit"] == 5


@pytest.mark.asyncio
async def test_reranked_service_skips_reranker_for_hard_filtered_result() -> None:
    retrieval = FakeRetrieval(
        RetrievalSearchResult(
            items=[],
            strategy="expanded-hybrid-rrf-v1",
            normalized_query="不存在",
            candidate_count=0,
            hard_filtered=True,
        )
    )
    reranker = FakeReranker()
    service = RerankedRetrievalService(retrieval, reranker)

    result = await service.search_evidence(query="不存在", limit=5)

    assert result.items == []
    assert result.hard_filtered is True
    assert reranker.calls == []


@pytest.mark.asyncio
async def test_reranked_service_propagates_reranker_errors() -> None:
    retrieval = FakeRetrieval(
        RetrievalSearchResult(
            items=[
                _evidence(
                    chunk_id=1,
                    title="静夜思",
                    text="举头望明月，低头思故乡。",
                    score=0.9,
                )
            ],
            strategy="expanded-hybrid-rrf-v1",
            normalized_query="明月",
            candidate_count=1,
        )
    )
    reranker = FakeReranker(error=RuntimeError("reranker failed"))
    service = RerankedRetrievalService(retrieval, reranker)

    with pytest.raises(RuntimeError, match="reranker failed"):
        await service.search_evidence(query="明月", limit=5)


@pytest.mark.asyncio
async def test_reranked_service_rejects_limit_above_candidate_limit() -> None:
    service = RerankedRetrievalService(
        FakeRetrieval(
            RetrievalSearchResult(
                items=[],
                strategy="expanded-hybrid-rrf-v1",
                normalized_query="明月",
                candidate_count=0,
            )
        ),
        FakeReranker(),
        candidate_limit=10,
    )

    with pytest.raises(ValueError, match="cannot exceed candidate_limit"):
        await service.search_evidence(query="明月", limit=11)
