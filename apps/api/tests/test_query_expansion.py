from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from app.core.errors import AppError, ErrorCode
from app.models.chunk import ChunkGranularity
from app.schemas.query_expansion import QueryExpansionLexicon
from app.schemas.retrieval import RetrievalEvidence
from app.services.query_expansion import (
    DEFAULT_QUERY_LEXICON_PATH,
    ExpandedRetrievalService,
    LexiconQueryRewriter,
    load_query_lexicon,
)
from app.services.retrieval import RetrievalSearchResult
from pydantic import ValidationError


@dataclass
class FakeRetrieval:
    results_by_query: dict[str, list[RetrievalEvidence]]
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
        items = self.results_by_query.get(query, [])
        return RetrievalSearchResult(
            items=items,
            strategy="fake-lexical",
            normalized_query=query.strip(),
            candidate_count=len(items),
        )


def _evidence(
    *,
    chunk_id: int,
    score: float = 0.8,
    match_types: list[str] | None = None,
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
        match_types=match_types or ["chunk_phrase"],
        published_at=None,
    )


def test_default_query_lexicon_is_versioned_and_loadable() -> None:
    lexicon = load_query_lexicon(DEFAULT_QUERY_LEXICON_PATH)

    assert lexicon.version == "query-lexicon-v1"
    assert [concept.name for concept in lexicon.concepts] == [
        "moon",
        "homesickness",
    ]


def test_query_lexicon_rejects_duplicate_terms(tmp_path: Path) -> None:
    path = tmp_path / "invalid-lexicon.json"
    path.write_text(
        json.dumps(
            {
                "version": "invalid-v1",
                "concepts": [
                    {
                        "name": "moon",
                        "triggers": ["月亮", "月亮"],
                        "expansions": ["明月"],
                    }
                ],
                "known_authors": ["李白"],
                "known_dynasties": ["唐"],
                "poetic_intent_terms": ["诗"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_query_lexicon(path)


@pytest.mark.asyncio
async def test_lexicon_rewriter_accepts_injected_lexicon() -> None:
    lexicon = QueryExpansionLexicon.model_validate(
        {
            "version": "test-v1",
            "concepts": [
                {
                    "name": "river",
                    "triggers": ["江河"],
                    "expansions": ["长江"],
                }
            ],
            "known_authors": ["李白"],
            "known_dynasties": ["唐"],
            "poetic_intent_terms": ["诗"],
        }
    )

    rewrite = await LexiconQueryRewriter(lexicon).rewrite("李白写江河的诗")

    assert rewrite.variants == ("李白", "长江")
    assert rewrite.matched_concepts == ("river",)


@pytest.mark.asyncio
async def test_lexicon_rewriter_expands_modern_language_and_entities() -> None:
    rewrite = await LexiconQueryRewriter().rewrite("苏轼关于中秋的词")

    assert rewrite.applied is True
    assert rewrite.variants == ("苏轼", "明月", "月光", "中秋")
    assert rewrite.matched_concepts == ("moon",)
    assert rewrite.matched_entities == ("苏轼",)


@pytest.mark.asyncio
async def test_lexicon_rewriter_deduplicates_variants_and_leaves_unknown_query() -> None:
    rewriter = LexiconQueryRewriter()

    expanded = await rewriter.rewrite("李白写月亮的诗句")
    untouched = await rewriter.rewrite("床前明月光")

    assert expanded.variants == ("李白", "明月", "月光", "中秋")
    assert expanded.matched_entities == ("李白",)
    assert untouched.variants == ()
    assert untouched.applied is False


@pytest.mark.asyncio
async def test_lexicon_rewriter_extracts_titles_and_quoted_lines() -> None:
    rewriter = LexiconQueryRewriter()

    title = await rewriter.rewrite("《春晓》描绘了春天清晨怎样的景象？")
    quote = await rewriter.rewrite(
        "“举头望明月，低头思故乡”表达了什么情感？"
    )

    assert title.variants == ("春晓",)
    assert quote.variants == ("举头望明月，低头思故乡",)


@pytest.mark.asyncio
async def test_expanded_retrieval_fuses_rewritten_queries_with_rrf() -> None:
    retrieval = FakeRetrieval(
        results_by_query={
            "李白写月亮的诗句": [],
            "李白": [
                _evidence(chunk_id=1, match_types=["author_phrase"]),
                _evidence(chunk_id=2),
            ],
            "明月": [
                _evidence(chunk_id=2, match_types=["chunk_phrase"]),
                _evidence(chunk_id=3),
            ],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query="李白写月亮的诗句", limit=3)

    assert result.strategy == "expanded-lexical-v1"
    assert result.candidate_count == 3
    assert [item.chunk_id for item in result.items] == [2, 1, 3]
    assert result.items[0].score == 0.396774
    assert result.items[0].match_types == [
        "chunk_phrase",
        "query_expansion",
        "rrf_fusion",
    ]
    assert all(call["limit"] == 15 for call in retrieval.calls)


@pytest.mark.asyncio
async def test_expanded_retrieval_forwards_filters_and_keeps_single_query_result() -> None:
    retrieval = FakeRetrieval(
        results_by_query={
            "床前明月光": [_evidence(chunk_id=7)],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(
        query="床前明月光",
        limit=5,
        granularities=[ChunkGranularity.LINE],
        author_id=7,
        dynasty_id=8,
    )

    assert result.strategy == "expanded-lexical-v1"
    assert [item.chunk_id for item in result.items] == [7]
    assert result.items[0].match_types == ["chunk_phrase"]
    assert retrieval.calls == [
        {
            "query": "床前明月光",
            "limit": 25,
            "granularities": [ChunkGranularity.LINE],
            "author_id": 7,
            "dynasty_id": 8,
        }
    ]


@pytest.mark.asyncio
async def test_expanded_retrieval_applies_the_limit_to_a_single_variant() -> None:
    retrieval = FakeRetrieval(
        results_by_query={
            "床前明月光": [
                _evidence(chunk_id=1),
                _evidence(chunk_id=2),
                _evidence(chunk_id=3),
            ],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query="床前明月光", limit=2)

    assert [item.chunk_id for item in result.items] == [1, 2]
    assert result.candidate_count == 3


@pytest.mark.asyncio
async def test_expanded_retrieval_rejects_blank_query_before_rewriting() -> None:
    retrieval = FakeRetrieval(results_by_query={})

    with pytest.raises(AppError) as error:
        await ExpandedRetrievalService(
            retrieval,
            LexiconQueryRewriter(),
        ).search_evidence(query="   ", limit=5)

    assert error.value.code == ErrorCode.VALIDATION_ERROR
    assert retrieval.calls == []
