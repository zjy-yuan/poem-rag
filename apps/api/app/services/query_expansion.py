from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.core.errors import AppError, ErrorCode
from app.core.text import normalize_lookup
from app.models.chunk import ChunkGranularity
from app.schemas.query_expansion import QueryExpansionLexicon
from app.schemas.retrieval import RetrievalEvidence
from app.services.retrieval import RetrievalSearchResult

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
_QUERY_EXPANSION_MATCH = "query_expansion"
_RRF_MATCH = "rrf_fusion"
_LITERAL_PATTERNS = (
    re.compile(r"《([^《》]{1,100})》"),
    re.compile(r"“([^“”]{1,100})”"),
    re.compile(r"‘([^‘’]{1,100})’"),
    re.compile(r'"([^"]{1,100})"'),
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

    @property
    def applied(self) -> bool:
        return bool(self.variants)


class QueryRewriter(Protocol):
    async def rewrite(self, query: str) -> QueryRewrite: ...


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

        variants.extend(_extract_literals(query))

        for author in self.lexicon.known_authors:
            if normalize_lookup(author) in normalized_query:
                matched_entities.append(author)
                variants.append(author)

        has_poetic_intent = _contains_any(
            normalized_query,
            self.lexicon.poetic_intent_terms,
        )
        if has_poetic_intent:
            for rule in self.lexicon.concepts:
                if not _contains_any(normalized_query, rule.triggers):
                    continue
                matched_concepts.append(rule.name)
                variants.extend(rule.expansions)

            for dynasty in self.lexicon.known_dynasties:
                if normalize_lookup(dynasty) in normalized_query:
                    matched_entities.append(dynasty)
                    variants.append(dynasty)

        return QueryRewrite(
            original_query=query,
            variants=_deduplicate_variants(variants),
            matched_concepts=tuple(matched_concepts),
            matched_entities=tuple(matched_entities),
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
    ) -> None:
        self.retrieval = retrieval
        self.rewriter = rewriter
        self.strategy_name = strategy_name

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
        variants = _deduplicate_variants([query, *rewrite.variants])
        branch_limit = _candidate_limit(limit)

        results: list[RetrievalSearchResult] = []
        for variant in variants:
            results.append(
                await self.retrieval.search_evidence(
                    query=variant,
                    limit=branch_limit,
                    granularities=granularities,
                    author_id=author_id,
                    dynasty_id=dynasty_id,
                )
            )

        if len(results) == 1:
            result = results[0]
            return RetrievalSearchResult(
                items=result.items[:limit],
                strategy=self.strategy_name,
                normalized_query=result.normalized_query,
                candidate_count=result.candidate_count,
            )

        fused: dict[int, _FusedCandidate] = {}
        for variant_index, result in enumerate(results):
            _add_variant(
                fused,
                result.items,
                from_expansion=variant_index > 0,
            )

        ranked = sorted(
            fused.values(),
            key=lambda item: (
                -item.rrf_score,
                _best_rank(item),
                item.evidence.chunk_id,
            ),
        )
        max_score = len(results) / (RRF_RANK_CONSTANT + 1)
        return RetrievalSearchResult(
            items=[
                _serialize(item, max_score=max_score, fused=True)
                for item in ranked[:limit]
            ],
            strategy=self.strategy_name,
            normalized_query=results[0].normalized_query,
            candidate_count=len(ranked),
        )


def _contains_any(normalized_query: str, terms: Sequence[str]) -> bool:
    return any(normalize_lookup(term) in normalized_query for term in terms)


def _extract_literals(query: str) -> list[str]:
    literals = [
        match.group(1).strip()
        for pattern in _LITERAL_PATTERNS
        for match in pattern.finditer(query)
    ]
    return list(_deduplicate_variants(literals))


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


def _candidate_limit(limit: int) -> int:
    return min(max(limit * _CANDIDATE_MULTIPLIER, limit), _MAX_CANDIDATE_LIMIT)


def _add_variant(
    fused: dict[int, _FusedCandidate],
    items: list[RetrievalEvidence],
    *,
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
        candidate.rrf_score += 1.0 / (RRF_RANK_CONSTANT + rank)


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
