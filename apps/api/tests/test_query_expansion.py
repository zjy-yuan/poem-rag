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
    QueryEntityFilters,
    load_query_lexicon,
)
from app.services.retrieval import RetrievalRequest, RetrievalSearchResult
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


@dataclass
class FakeBatchRetrieval(FakeRetrieval):
    batch_calls: list[list[RetrievalRequest]] = field(default_factory=list)

    async def search_evidence_batch(
        self,
        requests: list[RetrievalRequest],
    ) -> list[RetrievalSearchResult]:
        self.batch_calls.append(list(requests))
        return [
            await self.search_evidence(
                query=request.query,
                limit=request.limit,
                granularities=list(request.granularities) or None,
                author_id=request.author_id,
                dynasty_id=request.dynasty_id,
            )
            for request in requests
        ]


@dataclass
class FakeEntityResolver:
    filters: QueryEntityFilters
    calls: list[str] = field(default_factory=list)

    async def resolve(self, rewrite: Any) -> QueryEntityFilters:
        self.calls.append(rewrite.original_query)
        return self.filters


def _evidence(
    *,
    chunk_id: int,
    poem_id: int = 1,
    poem_version_id: int | None = None,
    title: str | None = None,
    granularity: ChunkGranularity = ChunkGranularity.LINE,
    text: str | None = None,
    score: float = 0.8,
    match_types: list[str] | None = None,
) -> RetrievalEvidence:
    return RetrievalEvidence(
        chunk_id=chunk_id,
        poem_id=poem_id,
        poem_version_id=poem_version_id or poem_id,
        annotation_id=None,
        annotation_type=None,
        title=title or f"Poem {poem_id}",
        author_id=1,
        author_name="李白",
        dynasty_id=1,
        dynasty_name="唐",
        granularity=granularity,
        chunk_index=chunk_id,
        text=text or f"Line {chunk_id}",
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
        "farewell",
        "integrity",
        "people_hardship",
        "lantern_festival",
        "longing",
        "white_hair_exaggeration",
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
async def test_lexicon_rewriter_separates_authors_from_dynasty_overlaps() -> None:
    rewriter = LexiconQueryRewriter()

    author_rewrite = await rewriter.rewrite("唐寅的诗")
    dynasty_rewrite = await rewriter.rewrite("汉代诗歌")

    assert author_rewrite.matched_authors == ("唐寅",)
    assert author_rewrite.matched_dynasties == ()
    assert dynasty_rewrite.matched_authors == ()
    assert dynasty_rewrite.matched_dynasties == ("汉",)


@pytest.mark.asyncio
async def test_lexicon_rewriter_marks_tail_evidence_queries() -> None:
    rewrite = await LexiconQueryRewriter().rewrite("《琵琶行》的结尾是什么")

    assert rewrite.explicit_titles == ("琵琶行",)
    assert rewrite.prefer_tail is True


@pytest.mark.asyncio
async def test_lexicon_rewriter_expands_lantern_festival_without_dynasty_false_match() -> None:
    rewrite = await LexiconQueryRewriter().rewrite("元宵词")

    assert rewrite.variants == ("元夕", "灯火", "花灯")
    assert rewrite.matched_concepts == ("lantern_festival",)
    assert rewrite.matched_entities == ()


@pytest.mark.asyncio
async def test_lexicon_rewriter_expands_white_hair_exaggeration() -> None:
    rewrite = await LexiconQueryRewriter().rewrite("哪句诗用夸张描写愁绪")

    assert rewrite.matched_concepts == ("white_hair_exaggeration",)
    assert {
        "白发三千丈",
        "白发",
        "三千丈",
        "缘愁",
        "似个长",
    } <= set(rewrite.variants)


@pytest.mark.asyncio
async def test_lexicon_rewriter_marks_explicit_multi_evidence_queries() -> None:
    rewrite = await LexiconQueryRewriter().rewrite("请找两首写月亮和思乡的诗")

    assert rewrite.multi_evidence is True


@pytest.mark.asyncio
async def test_lexicon_rewriter_excludes_multi_evidence_scaffolding_from_content_terms() -> None:
    rewrite = await LexiconQueryRewriter().rewrite(
        "请找两首分别写阳关送别和洛城思乡的唐诗，并说明情感。"
    )

    assert rewrite.multi_evidence is True
    assert "阳关送别" in rewrite.content_terms
    assert "洛城思乡" in rewrite.content_terms
    assert "两首" not in rewrite.content_terms
    assert "分别" not in rewrite.content_terms
    assert "两首分别" not in rewrite.content_terms


@pytest.mark.asyncio
async def test_lexicon_rewriter_balances_terms_across_multiple_subquestions() -> None:
    rewrite = await LexiconQueryRewriter().rewrite(
        "请找两首分别写个人气节和百姓疾苦的作品。"
    )

    assert rewrite.multi_evidence is True
    assert len(rewrite.content_term_groups) == 2
    assert "个人气节" in rewrite.content_term_groups[0]
    assert "气节" in rewrite.content_term_groups[0]
    assert "人杰" in rewrite.content_term_groups[0]
    assert "百姓疾苦" in rewrite.content_term_groups[1]
    assert "百姓" in rewrite.content_term_groups[1]
    assert "疾苦" in rewrite.content_term_groups[1]


@pytest.mark.asyncio
async def test_lexicon_rewriter_marks_two_explicit_titles_as_multi_evidence() -> None:
    rewrite = await LexiconQueryRewriter().rewrite(
        "《竹里馆》和《月夜忆舍弟》中的明月意象"
    )

    assert rewrite.explicit_titles == ("竹里馆", "月夜忆舍弟")
    assert rewrite.multi_evidence is True


@pytest.mark.asyncio
async def test_lexicon_rewriter_keeps_content_terms_when_a_concept_matches() -> None:
    rewrite = await LexiconQueryRewriter().rewrite("杜甫月夜思念分散兄弟的诗")

    assert rewrite.matched_concepts == ("longing",)
    assert "月夜" in rewrite.content_terms
    assert "分散" in rewrite.content_terms
    assert "兄弟" in rewrite.content_terms
    assert "思念" not in rewrite.content_terms
    assert "杜甫" not in rewrite.content_terms


@pytest.mark.asyncio
async def test_lexicon_rewriter_does_not_expand_cross_domain_questions() -> None:
    rewrite = await LexiconQueryRewriter().rewrite(
        "Docker 容器和虚拟机有什么区别"
    )

    assert rewrite.applied is False
    assert rewrite.variants == ()
    assert rewrite.content_terms == ()


@pytest.mark.asyncio
async def test_lexicon_rewriter_deduplicates_variants_and_leaves_unknown_query() -> None:
    rewriter = LexiconQueryRewriter()

    expanded = await rewriter.rewrite("李白写月亮的诗句")
    untouched = await rewriter.rewrite("床前明月光")

    assert expanded.variants == ("李白", "明月", "月光", "中秋")
    assert expanded.matched_entities == ("李白",)
    assert untouched.variants == ()
    assert untouched.applied is False
    assert untouched.multi_evidence is False


@pytest.mark.asyncio
async def test_lexicon_rewriter_extracts_titles_and_quoted_lines() -> None:
    rewriter = LexiconQueryRewriter()

    title = await rewriter.rewrite("《春晓》描绘了春天清晨怎样的景象？")
    quote = await rewriter.rewrite(
        "“举头望明月，低头思故乡”表达了什么情感？"
    )

    assert title.variants[0] == "春晓"
    assert "春天" in title.variants
    assert "清晨" in title.variants
    assert title.explicit_titles == ("春晓",)
    assert quote.variants == ("举头望明月，低头思故乡",)
    assert quote.explicit_titles == ()


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
    assert result.items[0].score == 0.365025
    assert result.items[0].match_types == [
        "chunk_phrase",
        "query_expansion",
        "rrf_fusion",
    ]
    assert retrieval.calls[0]["limit"] == 15
    assert retrieval.calls[1]["limit"] == 15
    assert retrieval.calls[2]["limit"] == 15


@pytest.mark.asyncio
async def test_expanded_retrieval_uses_batch_capability_once() -> None:
    retrieval = FakeBatchRetrieval(
        results_by_query={
            "李白写月亮的诗句": [],
            "李白": [_evidence(chunk_id=1)],
            "明月": [_evidence(chunk_id=2)],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query="李白写月亮的诗句", limit=3)

    assert result.candidate_count == 2
    assert len(retrieval.batch_calls) == 1
    assert len(retrieval.calls) == len(retrieval.batch_calls[0])
    assert [request.query for request in retrieval.batch_calls[0]][:2] == [
        "李白写月亮的诗句",
        "李白",
    ]


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
async def test_expanded_retrieval_resolves_entity_filters_and_keeps_caller_precedence() -> None:
    resolver = FakeEntityResolver(
        filters=QueryEntityFilters(author_id=7, dynasty_id=8)
    )
    retrieval = FakeRetrieval(
        results_by_query={
            "李白写月亮的诗句": [],
            "李白": [_evidence(chunk_id=1)],
            "明月": [_evidence(chunk_id=2)],
            "月光": [],
            "中秋": [],
        }
    )

    await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
        entity_resolver=resolver,
    ).search_evidence(
        query="李白写月亮的诗句",
        limit=2,
        author_id=99,
    )

    assert resolver.calls == ["李白写月亮的诗句"]
    assert retrieval.calls
    assert all(call["author_id"] == 99 for call in retrieval.calls)
    assert all(call["dynasty_id"] == 8 for call in retrieval.calls)


@pytest.mark.asyncio
async def test_expanded_retrieval_skips_resolved_entity_variants_for_structured_theme() -> None:
    resolver = FakeEntityResolver(
        filters=QueryEntityFilters(author_id=10, dynasty_id=1)
    )
    retrieval = FakeRetrieval(results_by_query={})

    await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
        entity_resolver=resolver,
    ).search_evidence(
        query="唐代白居易写爱情悲剧的长诗",
        limit=5,
    )

    called_queries = {call["query"] for call in retrieval.calls}
    assert "白居易" not in called_queries
    assert "唐" not in called_queries
    assert "爱情悲剧" in called_queries
    assert all(call["author_id"] == 10 for call in retrieval.calls)
    assert all(call["dynasty_id"] == 1 for call in retrieval.calls)


@pytest.mark.asyncio
async def test_expanded_retrieval_keeps_primary_text_for_structured_theme() -> None:
    resolver = FakeEntityResolver(
        filters=QueryEntityFilters(author_id=10, dynasty_id=1)
    )
    query = "唐代白居易写爱情悲剧的长诗"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "爱情悲剧": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    title="长恨歌",
                    granularity=ChunkGranularity.NOTE,
                    text="这首长诗写的是爱情悲剧。",
                ),
                _evidence(
                    chunk_id=101,
                    poem_id=10,
                    title="长恨歌",
                    granularity=ChunkGranularity.NOTE,
                    text="赏析一。",
                ),
                _evidence(
                    chunk_id=102,
                    poem_id=10,
                    title="长恨歌",
                    granularity=ChunkGranularity.NOTE,
                    text="赏析二。",
                ),
                _evidence(
                    chunk_id=103,
                    poem_id=10,
                    title="长恨歌",
                    granularity=ChunkGranularity.NOTE,
                    text="赏析三。",
                ),
                _evidence(
                    chunk_id=104,
                    poem_id=10,
                    title="长恨歌",
                    granularity=ChunkGranularity.NOTE,
                    text="赏析四。",
                ),
                _evidence(
                    chunk_id=105,
                    poem_id=10,
                    title="长恨歌",
                    granularity=ChunkGranularity.POEM,
                    text="天长地久有时尽，此恨绵绵无绝期。",
                ),
            ],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
        entity_resolver=resolver,
    ).search_evidence(query=query, limit=5)

    assert 105 in [item.chunk_id for item in result.items]
    assert len(result.items) == 5


@pytest.mark.asyncio
async def test_expanded_retrieval_prefers_the_tail_line_for_a_title_query() -> None:
    query = "《琵琶行》的结尾是什么"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "琵琶行": [
                _evidence(
                    chunk_id=1,
                    poem_id=10,
                    title="琵琶行",
                    text="浔阳江头夜送客，枫叶荻花秋瑟瑟。",
                ),
                _evidence(
                    chunk_id=46,
                    poem_id=11,
                    title="琵琶行 / 琵琶引",
                    text="座中泣下谁最多？江州司马青衫湿。",
                ),
            ],
            "结尾": [],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query=query, limit=1)

    assert [item.chunk_id for item in result.items] == [46]
    title_call = next(call for call in retrieval.calls if call["query"] == "琵琶行")
    assert title_call["limit"] == 200


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
async def test_expanded_retrieval_reserves_slots_for_multiple_poems() -> None:
    retrieval = FakeRetrieval(
        results_by_query={
            "请找两首写月亮和思乡的诗": [],
            "明月": [
                _evidence(chunk_id=1, poem_id=1),
                _evidence(chunk_id=2, poem_id=1),
                _evidence(chunk_id=3, poem_id=1),
                _evidence(chunk_id=10, poem_id=2),
            ],
            "月光": [_evidence(chunk_id=1, poem_id=1)],
            "中秋": [],
            "思乡": [
                _evidence(chunk_id=4, poem_id=1),
                _evidence(chunk_id=11, poem_id=2),
                _evidence(chunk_id=12, poem_id=2),
            ],
            "故乡": [_evidence(chunk_id=20, poem_id=3)],
            "天涯": [],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query="请找两首写月亮和思乡的诗", limit=5)

    assert len(result.items) == 5
    assert len({item.chunk_id for item in result.items}) == len(result.items)
    assert {item.poem_id for item in result.items} >= {1, 2}
    assert sum(item.poem_id == 1 for item in result.items) <= 2
    assert sum(item.poem_id == 2 for item in result.items) <= 2


@pytest.mark.asyncio
async def test_expanded_retrieval_reserves_a_candidate_for_each_subquestion() -> None:
    query = "请找两首分别写个人气节和百姓疾苦的作品。"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "个人气节": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    text="个人气节表现为宁死不屈。",
                )
            ],
            "百姓疾苦": [
                _evidence(
                    chunk_id=101,
                    poem_id=10,
                    text="百姓疾苦是这首作品的主题。",
                ),
                _evidence(
                    chunk_id=200,
                    poem_id=20,
                    text="兴，百姓苦；亡，百姓苦。",
                ),
            ],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query=query, limit=2)

    assert [item.poem_id for item in result.items] == [10, 20]


@pytest.mark.asyncio
async def test_expanded_retrieval_prefers_a_title_anchor_over_generic_terms() -> None:
    query = "请找两首分别写阳关送别和洛城思乡的唐诗，并说明情感。"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "思乡": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    title="苏幕遮·燎沉香",
                    granularity=ChunkGranularity.NOTE,
                    text="思乡、故乡与天涯是这首词的主题。",
                )
            ],
            "洛城": [
                _evidence(
                    chunk_id=200,
                    poem_id=20,
                    title="春夜洛城闻笛 / 春夜洛阳城闻笛",
                    text="散入春风满洛城，何人不起故园情。",
                )
            ],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query=query, limit=2)

    assert result.items[0].poem_id == 20


@pytest.mark.asyncio
async def test_expanded_retrieval_prefers_specific_terms_over_a_generic_anchor() -> None:
    query = "请找两首分别写阳关送别和洛城思乡的唐诗，并说明情感。"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "阳关": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    title="阳关曲·中秋月",
                    text="中秋作本名小秦王，入腔即阳关曲。",
                )
            ],
            "西出阳关": [
                _evidence(
                    chunk_id=200,
                    poem_id=20,
                    title="送元二使安西 / 渭城曲",
                    granularity=ChunkGranularity.POEM,
                    text=(
                        "渭城朝雨浥轻尘，客舍青青柳色新。"
                        "劝君更尽一杯酒，西出阳关无故人。"
                    ),
                )
            ],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query=query, limit=1)

    assert result.items[0].poem_id == 20


@pytest.mark.asyncio
async def test_expanded_retrieval_uses_full_poem_for_anchor_and_theme() -> None:
    query = "请找两首分别写阳关送别和洛城思乡的唐诗，并说明情感。"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "洛城": [
                _evidence(
                    chunk_id=100,
                    poem_id=20,
                    title="春夜洛城闻笛 / 春夜洛阳城闻笛",
                    text="谁家玉笛暗飞声，散入春风满洛城。",
                ),
                _evidence(
                    chunk_id=101,
                    poem_id=20,
                    title="春夜洛城闻笛 / 春夜洛阳城闻笛",
                    text="此夜曲中闻折柳，何人不起故园情。",
                ),
                _evidence(
                    chunk_id=102,
                    poem_id=20,
                    title="春夜洛城闻笛 / 春夜洛阳城闻笛",
                    granularity=ChunkGranularity.POEM,
                    text=(
                        "谁家玉笛暗飞声，散入春风满洛城。"
                        "此夜曲中闻折柳，何人不起故园情。"
                    ),
                ),
            ],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query=query, limit=1)

    assert result.items[0].chunk_id == 102


@pytest.mark.asyncio
async def test_expanded_retrieval_reserves_slots_for_explicit_titles() -> None:
    retrieval = FakeRetrieval(
        results_by_query={
            "请比较《竹里馆》和《月夜忆舍弟》中的明月意象。": [],
            "竹里馆": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    title="竹里馆",
                )
            ],
            "月夜忆舍弟": [
                _evidence(
                    chunk_id=101,
                    poem_id=11,
                    title="月夜忆舍弟",
                )
            ],
            "明月": [
                _evidence(chunk_id=1, poem_id=1, title="明月夜"),
                _evidence(chunk_id=2, poem_id=2, title="明月引"),
                _evidence(chunk_id=3, poem_id=3, title="明月篇"),
            ],
            "月光": [],
            "中秋": [],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(
        query="请比较《竹里馆》和《月夜忆舍弟》中的明月意象。",
        limit=3,
    )

    assert {item.poem_id for item in result.items} >= {10, 11}


@pytest.mark.asyncio
async def test_expanded_retrieval_prefers_overlapping_lines_for_explicit_titles() -> None:
    query = "《行路难三首》和《秋浦歌十七首》中的黄河与白发"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "行路难三首": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    title="行路难三首",
                    granularity=ChunkGranularity.NOTE,
                    text="黄河是这首诗中的重要意象。",
                ),
                _evidence(
                    chunk_id=101,
                    poem_id=10,
                    title="行路难三首",
                    text="欲渡黄河冰塞川，将登太行雪满山。",
                ),
            ],
            "秋浦歌十七首": [
                _evidence(
                    chunk_id=200,
                    poem_id=20,
                    title="秋浦歌十七首",
                    granularity=ChunkGranularity.NOTE,
                    text="白发这一意象写出了愁绪。",
                ),
                _evidence(
                    chunk_id=201,
                    poem_id=20,
                    title="秋浦歌十七首",
                    text="白发三千丈，缘愁似个长。",
                ),
            ],
            "黄河": [
                _evidence(
                    chunk_id=101,
                    poem_id=10,
                    title="行路难三首",
                    text="欲渡黄河冰塞川，将登太行雪满山。",
                )
            ],
            "白发": [
                _evidence(
                    chunk_id=201,
                    poem_id=20,
                    title="秋浦歌十七首",
                    text="白发三千丈，缘愁似个长。",
                )
            ],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query=query, limit=2)

    assert [item.chunk_id for item in result.items] == [101, 201]


@pytest.mark.asyncio
async def test_expanded_retrieval_prefers_primary_text_for_multi_evidence_titles() -> None:
    query = "《长恨歌》和《雨霖铃》分别怎样写离别"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "长恨歌": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    title="长恨歌",
                    granularity=ChunkGranularity.NOTE,
                    text="赏析块包含长生殿誓言。",
                    score=1.0,
                ),
                _evidence(
                    chunk_id=101,
                    poem_id=10,
                    title="长恨歌",
                    granularity=ChunkGranularity.POEM,
                    text="七月七日长生殿，夜半无人私语时。",
                    score=0.5,
                ),
            ],
            "雨霖铃": [
                _evidence(
                    chunk_id=200,
                    poem_id=20,
                    title="雨霖铃",
                    granularity=ChunkGranularity.NOTE,
                    text="赏析块包含杨柳残月。",
                    score=1.0,
                ),
                _evidence(
                    chunk_id=201,
                    poem_id=20,
                    title="雨霖铃",
                    granularity=ChunkGranularity.POEM,
                    text="今宵酒醒何处？杨柳岸，晓风残月。",
                    score=0.5,
                ),
            ],
            "送别": [],
            "离别": [],
            "折柳": [],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query=query, limit=2)

    assert [item.chunk_id for item in result.items] == [101, 201]


@pytest.mark.asyncio
async def test_expanded_retrieval_prefers_full_poem_over_line_for_multi_titles() -> None:
    query = "《长恨歌》和《雨霖铃》分别怎样写离别"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "长恨歌": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    title="长恨歌",
                    text="七月七日长生殿，夜半无人私语时。",
                    score=1.0,
                ),
                _evidence(
                    chunk_id=101,
                    poem_id=10,
                    title="长恨歌",
                    granularity=ChunkGranularity.POEM,
                    text=(
                        "七月七日长生殿，夜半无人私语时。"
                        "在天愿作比翼鸟，在地愿为连理枝。"
                    ),
                    score=0.5,
                ),
            ],
            "雨霖铃": [
                _evidence(
                    chunk_id=200,
                    poem_id=20,
                    title="雨霖铃",
                    text="今宵酒醒何处？杨柳岸，晓风残月。",
                    score=1.0,
                ),
                _evidence(
                    chunk_id=201,
                    poem_id=20,
                    title="雨霖铃",
                    granularity=ChunkGranularity.POEM,
                    text="寒蝉凄切，对长亭晚。今宵酒醒何处？杨柳岸，晓风残月。",
                    score=0.5,
                ),
            ],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query=query, limit=2)

    assert [item.chunk_id for item in result.items] == [101, 201]


@pytest.mark.asyncio
async def test_expanded_retrieval_reranks_a_single_explicit_title() -> None:
    query = "《行路难三首》中的黄河"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "行路难三首": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    title="行路难三首",
                    text="金樽清酒斗十千，玉盘珍羞直万钱。",
                ),
                _evidence(
                    chunk_id=101,
                    poem_id=10,
                    title="行路难三首",
                    text="欲渡黄河冰塞川，将登太行雪满山。",
                ),
            ],
            "黄河": [
                _evidence(
                    chunk_id=101,
                    poem_id=10,
                    title="行路难三首",
                    text="欲渡黄河冰塞川，将登太行雪满山。",
                )
            ],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query=query, limit=2)

    assert [item.chunk_id for item in result.items] == [101, 100]


@pytest.mark.asyncio
async def test_expanded_retrieval_keeps_the_full_poem_for_a_single_title() -> None:
    query = "《登高》如何写秋日所见"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "登高": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    title="登高",
                    granularity=ChunkGranularity.NOTE,
                    text="此诗通过秋日所见，抒发漂泊孤愁。",
                ),
                _evidence(
                    chunk_id=101,
                    poem_id=10,
                    title="登高",
                    granularity=ChunkGranularity.POEM,
                    text="风急天高猿啸哀，渚清沙白鸟飞回。",
                ),
            ],
            "秋日": [],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query=query, limit=2)

    assert [item.chunk_id for item in result.items] == [100, 101]


@pytest.mark.asyncio
async def test_expanded_retrieval_rejects_a_missing_explicit_title() -> None:
    query = "王维《相思》的译文是什么"
    retrieval = FakeRetrieval(
        results_by_query={
            query: [],
            "王维": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    title="山居秋暝",
                    text="空山新雨后，天气晚来秋。",
                )
            ],
            "相思": [
                _evidence(
                    chunk_id=200,
                    poem_id=20,
                    title="长相思·其一",
                    text="长相思，在长安。",
                )
            ],
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query=query, limit=5)

    assert result.items == []


@pytest.mark.asyncio
async def test_expanded_retrieval_skips_queries_for_unavailable_attributes() -> None:
    retrieval = FakeRetrieval(
        results_by_query={
            "《竹里馆》写于哪一年": [
                _evidence(
                    chunk_id=100,
                    poem_id=10,
                    title="竹里馆",
                    text="独坐幽篁里，弹琴复长啸。",
                )
            ]
        }
    )

    result = await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
    ).search_evidence(query="《竹里馆》写于哪一年", limit=5)

    assert result.items == []
    assert retrieval.calls == []


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


def test_expanded_retrieval_rejects_invalid_variant_limit() -> None:
    with pytest.raises(ValueError, match="max_variants must be at least 1"):
        ExpandedRetrievalService(
            FakeRetrieval(results_by_query={}),
            LexiconQueryRewriter(),
            max_variants=0,
        )


@pytest.mark.asyncio
async def test_expanded_retrieval_limits_variants_by_weight_and_keeps_query_order() -> None:
    retrieval = FakeRetrieval(results_by_query={})

    await ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
        max_variants=3,
    ).search_evidence(query="李白写月亮的诗句", limit=5)

    assert [call["query"] for call in retrieval.calls] == [
        "李白写月亮的诗句",
        "李白",
        "明月",
    ]
