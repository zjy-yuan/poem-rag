from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.core.text import normalize_content, normalize_lookup
from app.models.chunk import ChunkGranularity
from app.repositories.authors import AuthorRepository
from app.repositories.dynasties import DynastyRepository
from app.schemas.query_expansion import (
    QueryExpansionConcept,
    QueryExpansionLexicon,
)
from app.schemas.retrieval import RetrievalEvidence
from app.services.retrieval import (
    BatchEvidenceRetriever,
    RetrievalRequest,
    RetrievalSearchResult,
)

EXPANDED_LEXICAL_STRATEGY = "expanded-lexical-v1"
DEFAULT_QUERY_LEXICON_PATH = (
    Path(__file__).resolve().parents[4]
    / "data"
    / "query_expansion"
    / "lexicon_v1.json"
)

RRF_RANK_CONSTANT = 60
_CANDIDATE_MULTIPLIER = 5
_MAX_CANDIDATE_LIMIT = 200
_EXPLICIT_TITLE_CANDIDATE_LIMIT = 50
_EXPLICIT_TITLE_TAIL_CANDIDATE_LIMIT = 200
_MULTI_EVIDENCE_MAX_PER_POEM = 2
_ORIGINAL_QUERY_WEIGHT = 3.0
_LITERAL_QUERY_WEIGHT = 3.0
_EXPLICIT_TITLE_WEIGHT = 2.5
_ENTITY_QUERY_WEIGHT = 2.0
_CONCEPT_QUERY_WEIGHT = 1.5
_LONG_CONTENT_TERM_WEIGHT = 1.5
_SHORT_CONTENT_TERM_WEIGHT = 0.5
_DEFAULT_QUERY_WEIGHT = 1.0
_QUERY_EXPANSION_MATCH = "query_expansion"
_RRF_MATCH = "rrf_fusion"
_MULTI_EVIDENCE_TERMS = (
    "两首",
    "两篇",
    "多首",
    "多个作品",
    "分别",
    "各自",
    "对比",
    "比较",
)
_TAIL_EVIDENCE_TERMS = (
    "结尾",
    "最后",
    "末尾",
    "收尾",
    "尾句",
    "结束处",
)
_DYNASTY_CONTEXT_SUFFIXES = (
    "代",
    "朝",
    "诗",
    "词",
    "曲",
    "赋",
    "人",
    "时期",
    "时代",
    "文学",
    "作品",
)
_NATURAL_LANGUAGE_MARKERS = (
    "怎样",
    "怎么",
    "如何",
    "什么",
    "为什么",
    "哪些",
    "哪一",
    "描写",
    "表达",
    "关于",
    "中的",
    "诗句",
    "意象",
    "情感",
    "赏析",
    "译文",
    "意思",
    "写",
    "诗",
    "词",
    "曲",
)
_QUERY_TERM_STOPWORDS = (
    "请找",
    "请问",
    "帮我",
    "一下",
    "有关",
    "关于",
    "中的",
    "之中",
    "怎样",
    "怎么",
    "如何",
    "什么",
    "为什么",
    "哪些",
    "哪一",
    "描写",
    "夸张",
    "表达",
    "说明",
    "赏析",
    "译文",
    "意思",
    "诗句",
    "诗歌",
    "作品",
    "意象",
    "情感",
    "作者",
    "诗人",
    "朝代",
    "句子",
    "词句",
    "所见",
    "散曲",
    "诗人",
    "写于",
    "写在",
    "怎样",
    "怎么",
    "如何",
    "为了",
    "关于",
    "中的",
    "之中",
    "我们",
    "他们",
    "以及",
    "并且",
    "或者",
    "然后",
    "其中",
    "可以",
    "能够",
    "没有",
    "不是",
    "的",
    "和",
    "与",
    "及",
    "在",
    "中",
    "了",
    "是",
    "有",
    "哪",
    "年",
    "吗",
    "呢",
    "写",
    "诗",
    "词",
    "曲",
)
_CJK_SEQUENCE = re.compile(r"[\u3400-\u9fff]+")
_CJK_CHARACTER = re.compile(r"[\u3400-\u9fff]")
_MAX_QUERY_TERMS = 8
_MAX_MULTI_EVIDENCE_TERMS_PER_GROUP = 5
_LITERAL_PATTERNS = (
    re.compile(r"《([^《》]{1,100})》"),
    re.compile(r"“([^“”]{1,100})”"),
    re.compile(r"‘([^‘’]{1,100})’"),
    re.compile(r'"([^"]{1,100})"'),
)
_UNAVAILABLE_ATTRIBUTE_TERMS = (
    "写于哪一年",
    "创作于哪一年",
    "作于哪一年",
    "哪一年创作",
    "哪一年写成",
    "出生地",
    "出生于哪里",
    "出生在哪里",
    "生于何地",
    "生卒年",
)


class RetrievalBranch(Protocol):
    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
        granularities: list[ChunkGranularity] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
    ) -> RetrievalSearchResult: ...


@dataclass(frozen=True, slots=True)
class QueryRewrite:
    """A deterministic query rewrite with its provenance."""

    original_query: str
    variants: tuple[str, ...]
    matched_concepts: tuple[str, ...] = ()
    matched_entities: tuple[str, ...] = ()
    matched_authors: tuple[str, ...] = ()
    matched_dynasties: tuple[str, ...] = ()
    explicit_titles: tuple[str, ...] = ()
    content_terms: tuple[str, ...] = ()
    concept_terms: tuple[str, ...] = ()
    content_term_groups: tuple[tuple[str, ...], ...] = ()
    content_term_group_anchors: tuple[tuple[str, ...], ...] = ()
    multi_evidence: bool = False
    prefer_tail: bool = False

    @property
    def applied(self) -> bool:
        return bool(self.variants)


class QueryRewriter(Protocol):
    async def rewrite(self, query: str) -> QueryRewrite: ...


@dataclass(frozen=True, slots=True)
class QueryEntityFilters:
    """Database filters inferred from explicit query entities."""

    author_id: int | None = None
    dynasty_id: int | None = None


class QueryEntityResolver(Protocol):
    async def resolve(self, rewrite: QueryRewrite) -> QueryEntityFilters: ...


def load_query_lexicon(
    path: Path = DEFAULT_QUERY_LEXICON_PATH,
) -> QueryExpansionLexicon:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return QueryExpansionLexicon.model_validate(payload)


class LexiconQueryRewriter:
    """Expand a versioned poetry lexicon without calling a model.

    The default lexicon is loaded from data/query_expansion and can be replaced
    with a reviewed domain table or an LLM rewriter later.
    """

    def __init__(self, lexicon: QueryExpansionLexicon | None = None) -> None:
        self.lexicon = lexicon or load_query_lexicon()

    async def rewrite(self, query: str) -> QueryRewrite:
        normalized_query = normalize_lookup(query)
        if not normalized_query:
            return QueryRewrite(original_query=query, variants=())

        variants: list[str] = []
        matched_concepts: list[str] = []
        matched_entities: list[str] = []
        matched_authors: list[str] = []
        matched_dynasties: list[str] = []
        matched_concept_terms: list[str] = []
        concept_trigger_spans = _concept_trigger_spans(
            normalized_query,
            self.lexicon,
        )
        entity_spans = list(concept_trigger_spans)
        explicit_titles = _extract_title_literals(query)
        literal_variants = _extract_literals(query)

        variants.extend(literal_variants)

        for author in self.lexicon.known_authors:
            author_spans = _uncovered_term_spans(
                normalized_query,
                normalize_lookup(author),
                entity_spans,
            )
            if not author_spans:
                continue
            matched_authors.append(author)
            matched_entities.append(author)
            entity_spans.extend(author_spans)
            variants.append(author)

        has_poetic_intent = _contains_any(
            normalized_query,
            self.lexicon.poetic_intent_terms,
        )
        if has_poetic_intent or explicit_titles:
            for rule in self.lexicon.concepts:
                if not _contains_any(normalized_query, rule.triggers):
                    continue
                matched_concepts.append(rule.name)
                matched_concept_terms.extend(rule.triggers)
                matched_concept_terms.extend(rule.expansions)
                variants.extend(rule.expansions)

            for dynasty in self.lexicon.known_dynasties:
                dynasty_spans = _dynasty_term_spans(
                    normalized_query,
                    normalize_lookup(dynasty),
                    entity_spans,
                )
                if not dynasty_spans:
                    continue
                matched_dynasties.append(dynasty)
                matched_entities.append(dynasty)
                entity_spans.extend(dynasty_spans)
                variants.append(dynasty)

        has_quoted_literal = any(
            literal not in explicit_titles for literal in literal_variants
        )
        has_domain_signal = bool(
            has_poetic_intent
            or matched_concepts
            or matched_entities
            or explicit_titles
        )
        multi_evidence = _requests_multiple_evidence(
            normalized_query,
            explicit_titles,
        )
        content_terms = ()
        content_term_groups: tuple[tuple[str, ...], ...] = ()
        content_term_group_anchors: tuple[tuple[str, ...], ...] = ()
        if has_domain_signal and not has_quoted_literal:
            excluded_terms = (
                *explicit_titles,
                *matched_entities,
                *matched_concept_terms,
            )
            if multi_evidence:
                (
                    content_term_groups,
                    content_term_group_anchors,
                ) = _extract_multi_evidence_term_groups(
                    query,
                    excluded_terms=excluded_terms,
                    concepts=self.lexicon.concepts,
                )
                content_terms = _deduplicate_variants(
                    [
                        term
                        for group in content_term_groups
                        for term in group
                    ]
                    + [
                        term
                        for group in content_term_group_anchors
                        for term in group
                    ]
                )
            if not content_terms:
                content_terms = _extract_query_terms(
                    query,
                    excluded_terms=excluded_terms,
                )
        variants.extend(content_terms)

        return QueryRewrite(
            original_query=query,
            variants=_deduplicate_variants(variants),
            matched_concepts=tuple(matched_concepts),
            matched_entities=tuple(matched_entities),
            matched_authors=tuple(matched_authors),
            matched_dynasties=tuple(matched_dynasties),
            explicit_titles=explicit_titles,
            content_terms=content_terms,
            concept_terms=tuple(_deduplicate_variants(matched_concept_terms)),
            content_term_groups=content_term_groups,
            content_term_group_anchors=content_term_group_anchors,
            multi_evidence=multi_evidence,
            prefer_tail=_requests_tail_evidence(normalized_query),
        )


class RepositoryQueryEntityResolver:
    """Resolve unambiguous query entities to database IDs."""

    def __init__(self, session: AsyncSession) -> None:
        self.authors = AuthorRepository(session)
        self.dynasties = DynastyRepository(session)

    async def resolve(self, rewrite: QueryRewrite) -> QueryEntityFilters:
        author_id = None
        dynasty_id = None

        if len(rewrite.matched_authors) == 1 and not rewrite.multi_evidence:
            author = await self.authors.get_by_normalized_name(
                normalize_lookup(rewrite.matched_authors[0])
            )
            if author is not None and author.deleted_at is None:
                author_id = author.id

        if len(rewrite.matched_dynasties) == 1:
            dynasty = await self.dynasties.get_by_normalized_name(
                normalize_lookup(rewrite.matched_dynasties[0])
            )
            if dynasty is not None:
                dynasty_id = dynasty.id

        return QueryEntityFilters(
            author_id=author_id,
            dynasty_id=dynasty_id,
        )


@dataclass(slots=True)
class _FusedCandidate:
    evidence: RetrievalEvidence
    rrf_score: float = 0.0
    best_rank: int | None = None
    from_expansion: bool = False
    match_types: list[str] = field(default_factory=list)


class ExpandedRetrievalService:
    """Run several rewritten queries and fuse their ranked results with RRF."""

    def __init__(
        self,
        retrieval: RetrievalBranch,
        rewriter: QueryRewriter,
        *,
        strategy_name: str = EXPANDED_LEXICAL_STRATEGY,
        entity_resolver: QueryEntityResolver | None = None,
        max_variants: int | None = None,
    ) -> None:
        if max_variants is not None and max_variants < 1:
            raise ValueError("max_variants must be at least 1")
        self.retrieval = retrieval
        self.rewriter = rewriter
        self.strategy_name = strategy_name
        self.entity_resolver = entity_resolver
        self.max_variants = max_variants

    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
        granularities: list[ChunkGranularity] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
    ) -> RetrievalSearchResult:
        normalized_query = query.strip()
        if not normalized_query:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="查询内容不能为空",
            )

        rewrite = await self.rewriter.rewrite(query)
        if _requests_unavailable_attribute(normalize_lookup(query)):
            return RetrievalSearchResult(
                items=[],
                strategy=self.strategy_name,
                normalized_query=normalize_lookup(query),
                candidate_count=0,
                hard_filtered=True,
            )

        resolved_filters = (
            await self.entity_resolver.resolve(rewrite)
            if self.entity_resolver is not None
            else QueryEntityFilters()
        )
        effective_author_id = (
            author_id
            if author_id is not None
            else resolved_filters.author_id
        )
        effective_dynasty_id = (
            dynasty_id
            if dynasty_id is not None
            else resolved_filters.dynasty_id
        )

        weighted_variants = _weighted_variants(query, rewrite)
        if self.max_variants is not None:
            weighted_variants = _limit_weighted_variants(
                weighted_variants,
                self.max_variants,
            )
        branch_limit = _candidate_limit(limit)
        resolved_entity_keys = _resolved_entity_variant_keys(
            rewrite,
            resolved_filters=resolved_filters,
        )

        explicit_title_keys = {
            normalize_lookup(title)
            for title in rewrite.explicit_titles
        }
        variant_requests: list[tuple[str, float, RetrievalRequest]] = []
        for variant, weight in weighted_variants:
            if normalize_lookup(variant) in resolved_entity_keys:
                continue
            variant_limit = (
                max(
                    branch_limit,
                    (
                        _EXPLICIT_TITLE_TAIL_CANDIDATE_LIMIT
                        if rewrite.prefer_tail
                        else _EXPLICIT_TITLE_CANDIDATE_LIMIT
                    ),
                )
                if normalize_lookup(variant) in explicit_title_keys
                else branch_limit
            )
            variant_requests.append(
                (
                    variant,
                    weight,
                    RetrievalRequest(
                        query=variant,
                        limit=variant_limit,
                        granularities=tuple(granularities or ()),
                        author_id=effective_author_id,
                        dynasty_id=effective_dynasty_id,
                    ),
                )
            )

        if isinstance(self.retrieval, BatchEvidenceRetriever):
            batch_results = await self.retrieval.search_evidence_batch(
                [request for _, _, request in variant_requests]
            )
            if len(batch_results) != len(variant_requests):
                raise RuntimeError(
                    "batch retrieval returned an invalid result count"
                )
        else:
            batch_results = []
            for _, _, request in variant_requests:
                batch_results.append(
                    await self.retrieval.search_evidence(
                        query=request.query,
                        limit=request.limit,
                        granularities=list(request.granularities) or None,
                        author_id=request.author_id,
                        dynasty_id=request.dynasty_id,
                    )
                )

        results = [
            (variant, weight, result)
            for (variant, weight, _), result in zip(
                variant_requests,
                batch_results,
                strict=True,
            )
        ]

        if len(results) == 1:
            _, _, result = results[0]
            return RetrievalSearchResult(
                items=result.items[:limit],
                strategy=self.strategy_name,
                normalized_query=result.normalized_query,
                candidate_count=result.candidate_count,
                hard_filtered=result.hard_filtered,
            )

        fused: dict[int, _FusedCandidate] = {}
        normalized_query = normalize_lookup(query)
        for _, weight, result in results:
            _add_variant(
                fused,
                result.items,
                weight=weight,
                from_expansion=normalize_lookup(result.normalized_query)
                != normalized_query,
            )

        ranked = sorted(
            fused.values(),
            key=lambda item: (
                -item.rrf_score,
                _best_rank(item),
                item.evidence.chunk_id,
            ),
        )
        if rewrite.explicit_titles:
            selected = _select_explicit_title_candidates(
                ranked,
                limit=limit,
                target_titles=rewrite.explicit_titles,
                query_terms=_selection_terms(rewrite),
                diversify=rewrite.multi_evidence,
                prefer_tail=rewrite.prefer_tail,
                prefer_primary_text=rewrite.multi_evidence,
            )
        elif rewrite.multi_evidence:
            selected = _select_multi_evidence_candidates(
                ranked,
                limit=limit,
                target_titles=(),
                query_terms=_selection_terms(rewrite),
                query_term_groups=rewrite.content_term_groups,
                query_group_anchors=rewrite.content_term_group_anchors,
            )
        else:
            selected = _select_structured_theme_candidates(
                ranked,
                limit=limit,
                query_terms=_selection_terms(rewrite),
                prefer_primary_text=bool(
                    rewrite.content_terms
                    and (
                        effective_author_id is not None
                        or effective_dynasty_id is not None
                    )
                ),
            )
        hard_filtered = bool(rewrite.explicit_titles and not selected)
        max_score = sum(weight for _, weight, _ in results) / (
            RRF_RANK_CONSTANT + 1
        )
        return RetrievalSearchResult(
            items=[
                _serialize(item, max_score=max_score, fused=True)
                for item in selected
            ],
            strategy=self.strategy_name,
            normalized_query=results[0][2].normalized_query,
            candidate_count=len(ranked),
            hard_filtered=hard_filtered,
        )


def _contains_any(normalized_query: str, terms: Sequence[str]) -> bool:
    return any(normalize_lookup(term) in normalized_query for term in terms)


def _concept_trigger_spans(
    normalized_query: str,
    lexicon: QueryExpansionLexicon,
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for rule in lexicon.concepts:
        for trigger in rule.triggers:
            normalized_trigger = normalize_lookup(trigger)
            if not normalized_trigger:
                continue
            start = 0
            while True:
                index = normalized_query.find(normalized_trigger, start)
                if index < 0:
                    break
                spans.append((index, index + len(normalized_trigger)))
                start = index + 1
    return spans


def _uncovered_term_spans(
    normalized_query: str,
    normalized_term: str,
    covered_spans: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    if not normalized_term:
        return []
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = normalized_query.find(normalized_term, start)
        if index < 0:
            return spans
        end = index + len(normalized_term)
        if not any(
            index < covered_end and end > covered_start
            for covered_start, covered_end in covered_spans
        ):
            spans.append((index, end))
        start = index + 1


def _dynasty_term_spans(
    normalized_query: str,
    normalized_term: str,
    covered_spans: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    spans = _uncovered_term_spans(
        normalized_query,
        normalized_term,
        covered_spans,
    )
    if normalized_query == normalized_term:
        return spans
    return [
        span
        for span in spans
        if _has_dynasty_context(normalized_query, span=span)
    ]


def _has_dynasty_context(
    normalized_query: str,
    *,
    span: tuple[int, int],
) -> bool:
    suffix = normalized_query[span[1] :]
    return any(suffix.startswith(marker) for marker in _DYNASTY_CONTEXT_SUFFIXES)


def _requests_multiple_evidence(
    normalized_query: str,
    explicit_titles: Sequence[str],
) -> bool:
    return (
        any(term in normalized_query for term in _MULTI_EVIDENCE_TERMS)
        or len(explicit_titles) >= 2
    )


def _requests_tail_evidence(normalized_query: str) -> bool:
    return any(term in normalized_query for term in _TAIL_EVIDENCE_TERMS)


def _requests_unavailable_attribute(normalized_query: str) -> bool:
    return any(
        normalize_lookup(term) in normalized_query
        for term in _UNAVAILABLE_ATTRIBUTE_TERMS
    )


def _extract_query_terms(
    query: str,
    *,
    excluded_terms: Sequence[str],
) -> tuple[str, ...]:
    """Extract low-cost lexical terms from a natural-language question.

    Poetry questions often combine an entity with descriptive words that are
    not present in the static concept lexicon (for example "黄河和太行" or
    "竹林月夜幽居"). The extractor deliberately produces short n-grams rather
    than pretending to be a full Chinese word segmenter. RRF can then reward
    evidence that matches several of these terms while exact phrases remain
    the strongest signal.
    """

    normalized_query = normalize_lookup(query)
    if not _is_natural_language_query(normalized_query):
        return ()

    stripped = _strip_query_terms(query, excluded_terms=excluded_terms)
    terms: list[str] = []
    for sequence in _CJK_SEQUENCE.findall(stripped):
        _append_sequence_terms(
            terms,
            sequence,
            limit=_MAX_QUERY_TERMS,
        )
        if len(terms) >= _MAX_QUERY_TERMS:
            break
    return tuple(terms[:_MAX_QUERY_TERMS])


def _extract_multi_evidence_term_groups(
    query: str,
    *,
    excluded_terms: Sequence[str],
    concepts: Sequence[QueryExpansionConcept],
) -> tuple[tuple[tuple[str, ...], ...], tuple[tuple[str, ...], ...]]:
    """Extract balanced term groups from a multi-evidence question."""

    normalized_query = normalize_lookup(query)
    if not _is_natural_language_query(normalized_query):
        return (), ()

    clause = _extract_multi_evidence_clause(normalized_query)
    if not clause:
        return (), ()
    fragments = [
        fragment.strip()
        for fragment in re.split(r"以及|和|与|、", clause)
        if fragment.strip()
    ]
    if len(fragments) < 2:
        return (), ()

    limit_per_group = max(
        1,
        min(
            _MAX_MULTI_EVIDENCE_TERMS_PER_GROUP,
            _MAX_QUERY_TERMS // len(fragments),
        ),
    )
    if len(fragments) <= 2:
        limit_per_group = _MAX_MULTI_EVIDENCE_TERMS_PER_GROUP
    groups: list[tuple[str, ...]] = []
    anchors: list[tuple[str, ...]] = []
    for fragment in fragments:
        terms = _extract_fragment_terms(
            fragment,
            excluded_terms=excluded_terms,
            concepts=concepts,
            limit=limit_per_group,
        )
        if terms:
            groups.append(terms)
            anchors.append(
                _extract_fragment_anchor_terms(
                    fragment,
                    concepts=concepts,
                )
            )
    return tuple(groups), tuple(anchors)


def _extract_multi_evidence_clause(normalized_query: str) -> str:
    marker = "分别"
    start = normalized_query.find(marker)
    start = start + len(marker) if start >= 0 else 0

    write_marker_positions = [
        position
        for candidate in ("描写", "关于", "写")
        if (position := normalized_query.find(candidate, start)) >= 0
    ]
    if write_marker_positions:
        write_position = min(write_marker_positions)
        start = write_position + (
            len("描写")
            if normalized_query.startswith("描写", write_position)
            else len("关于")
            if normalized_query.startswith("关于", write_position)
            else len("写")
        )

    end = _find_multi_evidence_clause_end(normalized_query, start=start)
    return normalized_query[start:end].strip()


def _find_multi_evidence_clause_end(
    normalized_query: str,
    *,
    start: int,
) -> int:
    end_markers = (
        "的作品",
        "的唐诗",
        "的宋词",
        "的元曲",
        "的诗",
        "的词",
        "的曲",
        "并说明",
        "并解释",
        "并分析",
        "并比较",
        "，",
        ",",
        "；",
        ";",
        "。",
    )
    positions = [
        position
        for marker in end_markers
        if (position := normalized_query.find(marker, start)) >= 0
    ]
    if positions:
        return min(positions)

    fallback = normalized_query.find("的", start)
    return fallback if fallback >= 0 else len(normalized_query)


def _extract_fragment_terms(
    fragment: str,
    *,
    excluded_terms: Sequence[str],
    concepts: Sequence[QueryExpansionConcept],
    limit: int,
) -> tuple[str, ...]:
    normalized_fragment = normalize_content(fragment)
    if not normalized_fragment:
        return ()

    terms: list[str] = []
    _append_query_term(terms, normalized_fragment)
    for term in _concept_terms_for_fragment(fragment, concepts=concepts):
        _append_query_term(terms, term)
        if len(terms) >= limit:
            break
    stripped = _strip_query_terms(fragment, excluded_terms=excluded_terms)
    for sequence in _CJK_SEQUENCE.findall(stripped):
        _append_sequence_terms(terms, sequence, limit=limit)
        if len(terms) >= limit:
            break
    return tuple(terms[:limit])


def _concept_terms_for_fragment(
    fragment: str,
    *,
    concepts: Sequence[QueryExpansionConcept],
) -> tuple[str, ...]:
    normalized_fragment = normalize_lookup(fragment)
    terms: list[str] = []
    for concept in concepts:
        if not _contains_any(normalized_fragment, concept.triggers):
            continue
        for expansion in concept.expansions:
            if expansion not in terms:
                terms.append(expansion)
    return tuple(terms)


def _extract_fragment_anchor_terms(
    fragment: str,
    *,
    concepts: Sequence[QueryExpansionConcept],
) -> tuple[str, ...]:
    """Extract a short, work-identifying anchor from a sub-question.

    ``洛城思乡`` should contribute ``洛城`` as an anchor, while the generic
    expansion terms remain in the normal group. For fragments such as
    ``阳关送别`` all tokens are known concept terms, so the leading bigram is
    retained as a conservative fallback.
    """

    expansion_terms = tuple(
        expansion
        for concept in concepts
        for expansion in concept.expansions
    )
    stripped = _strip_query_terms(
        fragment,
        excluded_terms=expansion_terms,
    )
    anchors: list[str] = []
    for sequence in _CJK_SEQUENCE.findall(stripped):
        _append_sequence_terms(anchors, sequence, limit=2)
        if anchors:
            break
    if anchors:
        return tuple(anchors[:2])

    normalized_fragment = normalize_lookup(fragment)
    for sequence in _CJK_SEQUENCE.findall(normalized_fragment):
        if len(sequence) >= 2:
            return (sequence[:2],)
    return ()


def _strip_query_terms(
    text: str,
    *,
    excluded_terms: Sequence[str],
) -> str:
    stripped = text
    stopwords = sorted(
        {
            *_QUERY_TERM_STOPWORDS,
            *_MULTI_EVIDENCE_TERMS,
            *excluded_terms,
        },
        key=len,
        reverse=True,
    )
    for stopword in stopwords:
        if stopword:
            stripped = stripped.replace(stopword, " ")
    return stripped


def _append_sequence_terms(
    terms: list[str],
    sequence: str,
    *,
    limit: int,
) -> None:
    if len(sequence) <= 4:
        _append_query_term(terms, sequence)
    for size in (2, 3):
        for index in range(0, len(sequence) - size + 1):
            _append_query_term(terms, sequence[index : index + size])
            if len(terms) >= limit:
                return


def _is_natural_language_query(normalized_query: str) -> bool:
    return len(normalized_query) >= 8 and any(
        marker in normalized_query for marker in _NATURAL_LANGUAGE_MARKERS
    )


def _append_query_term(terms: list[str], term: str) -> None:
    normalized = normalize_lookup(term)
    if len(normalized) < 2 or normalized in terms:
        return
    terms.append(normalized)


def _extract_literals(query: str) -> list[str]:
    literals = [
        match.group(1).strip()
        for pattern in _LITERAL_PATTERNS
        for match in pattern.finditer(query)
    ]
    return list(_deduplicate_variants(literals))


def _extract_title_literals(query: str) -> tuple[str, ...]:
    titles = [
        match.group(1).strip()
        for match in _LITERAL_PATTERNS[0].finditer(query)
    ]
    return _deduplicate_variants(titles)


def _deduplicate_variants(variants: list[str]) -> tuple[str, ...]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        normalized = normalize_lookup(variant)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduplicated.append(variant)
    return tuple(deduplicated)


def _selection_terms(rewrite: QueryRewrite) -> tuple[str, ...]:
    excluded = {
        normalize_lookup(title)
        for title in rewrite.explicit_titles
    }
    for terms in (
        rewrite.content_terms,
        rewrite.concept_terms,
        rewrite.variants,
    ):
        selected = tuple(
            term
            for term in _deduplicate_variants(list(terms))
            if normalize_lookup(term) not in excluded
        )
        if selected:
            return selected
    return ()


def _weighted_variants(
    query: str,
    rewrite: QueryRewrite,
) -> tuple[tuple[str, float], ...]:
    normalized_query = normalize_lookup(query)
    explicit_titles = {
        normalize_lookup(title)
        for title in rewrite.explicit_titles
    }
    quoted_literals = {
        normalize_lookup(literal)
        for literal in _extract_literals(query)
        if normalize_lookup(literal) not in explicit_titles
    }
    entities = {
        normalize_lookup(entity)
        for entity in rewrite.matched_entities
    }
    concepts = {
        normalize_lookup(term)
        for term in rewrite.concept_terms
    }
    content_terms = {
        normalize_lookup(term)
        for term in rewrite.content_terms
    }

    weighted: list[tuple[str, float]] = []
    seen: set[str] = set()
    for variant in (query, *rewrite.variants):
        normalized = normalize_lookup(variant)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        weighted.append(
            (
                variant,
                _variant_weight(
                    normalized,
                    normalized_query=normalized_query,
                    explicit_titles=explicit_titles,
                    quoted_literals=quoted_literals,
                    entities=entities,
                    concepts=concepts,
                    content_terms=content_terms,
                ),
            )
        )
    return tuple(weighted)


def _variant_weight(
    normalized_variant: str,
    *,
    normalized_query: str,
    explicit_titles: set[str],
    quoted_literals: set[str],
    entities: set[str],
    concepts: set[str],
    content_terms: set[str],
) -> float:
    if normalized_variant == normalized_query:
        return _ORIGINAL_QUERY_WEIGHT
    if normalized_variant in explicit_titles:
        return _EXPLICIT_TITLE_WEIGHT
    if normalized_variant in quoted_literals:
        return _LITERAL_QUERY_WEIGHT
    if normalized_variant in entities:
        return _ENTITY_QUERY_WEIGHT
    if normalized_variant in concepts:
        return _CONCEPT_QUERY_WEIGHT
    if normalized_variant in content_terms:
        return (
            _LONG_CONTENT_TERM_WEIGHT
            if len(normalized_variant) >= 4
            else _SHORT_CONTENT_TERM_WEIGHT
        )
    return _DEFAULT_QUERY_WEIGHT


def _limit_weighted_variants(
    weighted_variants: tuple[tuple[str, float], ...],
    limit: int,
) -> tuple[tuple[str, float], ...]:
    """Keep the highest-value variants while preserving query order."""

    if limit < 1:
        raise ValueError("limit must be at least 1")
    if len(weighted_variants) <= limit:
        return weighted_variants

    ranked_indexes = sorted(
        range(len(weighted_variants)),
        key=lambda index: (-weighted_variants[index][1], index),
    )
    selected_indexes = set(ranked_indexes[:limit])
    return tuple(
        variant
        for index, variant in enumerate(weighted_variants)
        if index in selected_indexes
    )


def _candidate_limit(limit: int) -> int:
    return min(max(limit * _CANDIDATE_MULTIPLIER, limit), _MAX_CANDIDATE_LIMIT)


def _resolved_entity_variant_keys(
    rewrite: QueryRewrite,
    *,
    resolved_filters: QueryEntityFilters,
) -> set[str]:
    """Avoid re-fusing entity-only queries after a database filter is active."""

    if (
        not rewrite.content_terms
        or rewrite.explicit_titles
        or rewrite.multi_evidence
    ):
        return set()

    resolved_entities: list[str] = []
    if resolved_filters.author_id is not None:
        resolved_entities.extend(rewrite.matched_authors)
    if resolved_filters.dynasty_id is not None:
        resolved_entities.extend(rewrite.matched_dynasties)
    return {
        normalize_lookup(entity)
        for entity in resolved_entities
        if normalize_lookup(entity)
    }


def _select_structured_theme_candidates(
    ranked: Sequence[_FusedCandidate],
    *,
    limit: int,
    query_terms: Sequence[str],
    prefer_primary_text: bool,
) -> list[_FusedCandidate]:
    """Keep the best poem text visible for structured theme queries."""

    selected = list(ranked[:limit])
    if not prefer_primary_text or limit <= 1 or not query_terms:
        return selected

    selected_chunk_ids = {
        candidate.evidence.chunk_id
        for candidate in selected
    }
    anchor = next(
        (
            candidate
            for candidate in selected
            if candidate.evidence.granularity == ChunkGranularity.NOTE
            and _content_overlap(candidate.evidence.text, query_terms) > 0
        ),
        None,
    )
    if anchor is None:
        return selected

    primary_text = min(
        (
            candidate
            for candidate in ranked
            if candidate.evidence.poem_id == anchor.evidence.poem_id
            and candidate.evidence.chunk_id not in selected_chunk_ids
            and candidate.evidence.granularity
            in (ChunkGranularity.POEM, ChunkGranularity.LINE)
        ),
        key=lambda candidate: (
            0
            if candidate.evidence.granularity == ChunkGranularity.POEM
            else 1,
            -_content_overlap(candidate.evidence.text, query_terms),
            -candidate.rrf_score,
            _best_rank(candidate),
            candidate.evidence.chunk_id,
        ),
        default=None,
    )
    if primary_text is None:
        return selected

    selected.insert(selected.index(anchor) + 1, primary_text)
    return selected[:limit]


def _select_multi_evidence_candidates(
    ranked: Sequence[_FusedCandidate],
    *,
    limit: int,
    target_titles: Sequence[str],
    query_terms: Sequence[str],
    query_term_groups: Sequence[Sequence[str]] = (),
    query_group_anchors: Sequence[Sequence[str]] = (),
) -> list[_FusedCandidate]:
    selected: list[_FusedCandidate] = []
    selected_chunk_ids: set[int] = set()
    counts_by_poem: dict[int, int] = {}

    for target_title in target_titles:
        candidate = _find_title_candidate(
            ranked,
            target_title=target_title,
            selected_chunk_ids=selected_chunk_ids,
            query_terms=query_terms,
        )
        if candidate is None:
            continue
        selected.append(candidate)
        selected_chunk_ids.add(candidate.evidence.chunk_id)
        poem_id = candidate.evidence.poem_id
        counts_by_poem[poem_id] = counts_by_poem.get(poem_id, 0) + 1
        if len(selected) >= limit:
            return selected

    for group_index, term_group in enumerate(query_term_groups):
        group_anchors = (
            query_group_anchors[group_index]
            if group_index < len(query_group_anchors)
            else ()
        )
        candidate = _find_group_candidate(
            ranked,
            group_terms=term_group,
            group_anchors=group_anchors,
            selected_chunk_ids=selected_chunk_ids,
            excluded_poem_ids=set(counts_by_poem),
        )
        if candidate is None:
            candidate = _find_group_candidate(
                ranked,
                group_terms=term_group,
                group_anchors=group_anchors,
                selected_chunk_ids=selected_chunk_ids,
                excluded_poem_ids=set(),
            )
        if candidate is None:
            continue
        selected.append(candidate)
        selected_chunk_ids.add(candidate.evidence.chunk_id)
        poem_id = candidate.evidence.poem_id
        counts_by_poem[poem_id] = counts_by_poem.get(poem_id, 0) + 1
        if len(selected) >= limit:
            return selected

    for candidate in ranked:
        poem_id = candidate.evidence.poem_id
        if candidate.evidence.chunk_id in selected_chunk_ids:
            continue
        if counts_by_poem.get(poem_id, 0) >= _MULTI_EVIDENCE_MAX_PER_POEM:
            continue
        selected.append(candidate)
        selected_chunk_ids.add(candidate.evidence.chunk_id)
        counts_by_poem[poem_id] = counts_by_poem.get(poem_id, 0) + 1
        if len(selected) >= limit:
            return selected

    for candidate in ranked:
        if candidate.evidence.chunk_id in selected_chunk_ids:
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def _find_group_candidate(
    ranked: Sequence[_FusedCandidate],
    *,
    group_terms: Sequence[str],
    group_anchors: Sequence[str],
    selected_chunk_ids: set[int],
    excluded_poem_ids: set[int],
) -> _FusedCandidate | None:
    candidates = [
        candidate
        for candidate in ranked
        if candidate.evidence.chunk_id not in selected_chunk_ids
        and candidate.evidence.poem_id not in excluded_poem_ids
    ]
    scored = [
        (
            candidate,
            _group_candidate_score(
                candidate,
                group_terms,
                group_anchors=group_anchors,
            ),
        )
        for candidate in candidates
    ]
    scored = [item for item in scored if item[1] > 0]
    if not scored:
        return None
    best_score = max(score for _, score in scored)
    near_best = [
        item for item in scored if item[1] >= best_score * 0.75
    ]
    return min(
        (candidate for candidate, _ in near_best),
        key=lambda candidate: (
            _granularity_priority(candidate),
            -_group_candidate_score(
                candidate,
                group_terms,
                group_anchors=group_anchors,
            ),
            -candidate.rrf_score,
            _best_rank(candidate),
            candidate.evidence.chunk_id,
        ),
    )


def _group_candidate_score(
    candidate: _FusedCandidate,
    group_terms: Sequence[str],
    *,
    group_anchors: Sequence[str],
) -> int:
    """Prefer work-specific anchors over broad thematic expansions."""

    text_score = _content_overlap(candidate.evidence.text, group_terms)
    title_score = _content_overlap(candidate.evidence.title, group_terms)
    anchor_text_score = _content_overlap(
        candidate.evidence.text,
        group_anchors,
    )
    anchor_title_score = _content_overlap(
        candidate.evidence.title,
        group_anchors,
    )
    semantic_score = text_score + title_score
    anchor_score = (anchor_text_score * 2) + (anchor_title_score * 3)
    anchor_multiplier = 2 if anchor_score else 1
    return (semantic_score * anchor_multiplier) + anchor_score


def _select_explicit_title_candidates(
    ranked: Sequence[_FusedCandidate],
    *,
    limit: int,
    target_titles: Sequence[str],
    query_terms: Sequence[str],
    diversify: bool,
    prefer_tail: bool = False,
    prefer_primary_text: bool = False,
) -> list[_FusedCandidate]:
    selected: list[_FusedCandidate] = []
    selected_chunk_ids: set[int] = set()
    counts_by_poem: dict[int, int] = {}

    for target_title in target_titles:
        candidate = _find_title_candidate(
            ranked,
            target_title=target_title,
            selected_chunk_ids=selected_chunk_ids,
            query_terms=query_terms,
            prefer_tail=prefer_tail,
            prefer_primary_text=prefer_primary_text,
        )
        if candidate is None:
            return []
        selected.append(candidate)
        selected_chunk_ids.add(candidate.evidence.chunk_id)
        poem_id = candidate.evidence.poem_id
        counts_by_poem[poem_id] = counts_by_poem.get(poem_id, 0) + 1
        if len(selected) >= limit:
            return selected

    if not diversify and selected and len(selected) < limit:
        poem_candidate = next(
            (
                candidate
                for candidate in ranked
                if candidate.evidence.chunk_id not in selected_chunk_ids
                and candidate.evidence.poem_id == selected[0].evidence.poem_id
                and candidate.evidence.granularity == ChunkGranularity.POEM
            ),
            None,
        )
        if poem_candidate is not None:
            selected.append(poem_candidate)
            selected_chunk_ids.add(poem_candidate.evidence.chunk_id)
            counts_by_poem[poem_candidate.evidence.poem_id] = (
                counts_by_poem.get(poem_candidate.evidence.poem_id, 0) + 1
            )

    for candidate in ranked:
        poem_id = candidate.evidence.poem_id
        if candidate.evidence.chunk_id in selected_chunk_ids:
            continue
        if (
            diversify
            and counts_by_poem.get(poem_id, 0) >= _MULTI_EVIDENCE_MAX_PER_POEM
        ):
            continue
        selected.append(candidate)
        selected_chunk_ids.add(candidate.evidence.chunk_id)
        counts_by_poem[poem_id] = counts_by_poem.get(poem_id, 0) + 1
        if len(selected) >= limit:
            return selected
    return selected


def _find_title_candidate(
    ranked: Sequence[_FusedCandidate],
    *,
    target_title: str,
    selected_chunk_ids: set[int],
    query_terms: Sequence[str],
    prefer_tail: bool = False,
    prefer_primary_text: bool = False,
) -> _FusedCandidate | None:
    normalized_title = normalize_lookup(target_title)
    if not normalized_title:
        return None

    matches = [
        candidate
        for candidate in ranked
        if candidate.evidence.chunk_id not in selected_chunk_ids
        and _is_acceptable_title_match(
            candidate_title=candidate.evidence.title,
            target_title=target_title,
        )
    ]
    if matches:
        return _select_title_match(
            matches,
            query_terms=query_terms,
            prefer_tail=prefer_tail,
            prefer_primary_text=prefer_primary_text,
        )
    return None


def _is_acceptable_title_match(
    *,
    candidate_title: str,
    target_title: str,
) -> bool:
    normalized_candidate = normalize_lookup(candidate_title)
    normalized_target = normalize_lookup(target_title)
    if normalized_candidate == normalized_target:
        return True
    if not normalized_candidate.startswith(normalized_target):
        return False

    suffix = normalized_candidate[len(normalized_target) :]
    return bool(suffix) and _CJK_CHARACTER.match(suffix[0]) is None


def _select_title_match(
    candidates: Sequence[_FusedCandidate],
    *,
    query_terms: Sequence[str],
    prefer_tail: bool = False,
    prefer_primary_text: bool = False,
) -> _FusedCandidate:
    if prefer_tail:
        tail_candidates = [
            candidate
            for candidate in candidates
            if candidate.evidence.line_start is not None
        ]
        if tail_candidates:
            return min(
                tail_candidates,
                key=lambda candidate: (
                    -(candidate.evidence.line_start or 0),
                    -_content_overlap(candidate.evidence.text, query_terms),
                    -candidate.rrf_score,
                    _best_rank(candidate),
                    candidate.evidence.chunk_id,
                ),
            )

    if prefer_primary_text:
        primary_text_candidates = [
            candidate
            for candidate in candidates
            if candidate.evidence.granularity != ChunkGranularity.NOTE
        ]
        if primary_text_candidates:
            candidates = primary_text_candidates
            overlapping_poems = [
                candidate
                for candidate in candidates
                if candidate.evidence.granularity == ChunkGranularity.POEM
                and _content_overlap(candidate.evidence.text, query_terms) > 0
            ]
            if overlapping_poems:
                candidates = overlapping_poems

    overlapping = [
        candidate
        for candidate in candidates
        if _content_overlap(candidate.evidence.text, query_terms) > 0
    ]
    if overlapping:
        return min(
            overlapping,
            key=lambda candidate: (
                -_content_overlap(candidate.evidence.text, query_terms),
                -candidate.rrf_score,
                _granularity_priority(candidate),
                _best_rank(candidate),
                candidate.evidence.chunk_id,
            ),
        )

    poems = [
        candidate
        for candidate in candidates
        if candidate.evidence.granularity == ChunkGranularity.POEM
    ]
    if poems:
        return min(
            poems,
            key=lambda candidate: (
                -candidate.rrf_score,
                _best_rank(candidate),
                candidate.evidence.chunk_id,
            ),
        )

    non_notes = [
        candidate
        for candidate in candidates
        if candidate.evidence.granularity != ChunkGranularity.NOTE
    ]
    if non_notes:
        return min(
            non_notes,
            key=lambda candidate: (
                -candidate.rrf_score,
                _granularity_priority(candidate),
                _best_rank(candidate),
                candidate.evidence.chunk_id,
            ),
        )
    return min(
        candidates,
        key=lambda candidate: (
            -candidate.rrf_score,
            _best_rank(candidate),
            candidate.evidence.chunk_id,
        ),
    )


def _content_overlap(text: str, query_terms: Sequence[str]) -> int:
    normalized_text = normalize_lookup(text)
    return sum(
        len(normalize_lookup(term)) ** 2
        for term in query_terms
        if term and normalize_lookup(term) in normalized_text
    )


def _granularity_priority(candidate: _FusedCandidate) -> int:
    return {
        ChunkGranularity.LINE: 0,
        ChunkGranularity.POEM: 1,
        ChunkGranularity.NOTE: 2,
    }.get(candidate.evidence.granularity, 99)


def _add_variant(
    fused: dict[int, _FusedCandidate],
    items: list[RetrievalEvidence],
    *,
    weight: float,
    from_expansion: bool,
) -> None:
    for rank, evidence in enumerate(items, start=1):
        candidate = fused.get(evidence.chunk_id)
        if candidate is None:
            candidate = _FusedCandidate(evidence=evidence)
            fused[evidence.chunk_id] = candidate
        if candidate.best_rank is None or rank < candidate.best_rank:
            candidate.best_rank = rank
        if from_expansion:
            candidate.from_expansion = True
        for match_type in evidence.match_types:
            _append_unique(candidate.match_types, match_type)
        candidate.rrf_score += weight / (RRF_RANK_CONSTANT + rank)


def _best_rank(candidate: _FusedCandidate) -> int:
    return candidate.best_rank if candidate.best_rank is not None else 10**9


def _serialize(
    candidate: _FusedCandidate,
    *,
    max_score: float,
    fused: bool,
) -> RetrievalEvidence:
    match_types = list(candidate.match_types)
    if candidate.from_expansion:
        _append_unique(match_types, _QUERY_EXPANSION_MATCH)
    if fused:
        _append_unique(match_types, _RRF_MATCH)

    return candidate.evidence.model_copy(
        update={
            "score": round(min(1.0, candidate.rrf_score / max_score), 6),
            "match_types": match_types,
        }
    )


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
