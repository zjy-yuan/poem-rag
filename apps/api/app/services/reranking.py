from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.core.text import normalize_content, normalize_lookup
from app.models.chunk import ChunkGranularity
from app.schemas.retrieval import RetrievalEvidence
from app.services.retrieval import EvidenceRetriever, RetrievalSearchResult

RERANKED_HYBRID_STRATEGY = "expanded-hybrid-rerank-v1"
DETERMINISTIC_EVIDENCE_RERANKER = "deterministic-evidence-v1"

_RERANK_MATCH = "deterministic_rerank"
_DEFAULT_MAX_PER_POEM = 2
_TITLE_SEPARATOR = re.compile(r"[/／|｜·・]")


class EvidenceReranker(Protocol):
    async def rerank(
        self,
        *,
        query: str,
        evidence: list[RetrievalEvidence],
        limit: int,
    ) -> list[RetrievalEvidence]: ...


@dataclass(frozen=True, slots=True)
class _ScoredEvidence:
    evidence: RetrievalEvidence
    score: float
    upstream_index: int


class DeterministicEvidenceReranker:
    """Rank candidates with deterministic text and metadata signals."""

    name = DETERMINISTIC_EVIDENCE_RERANKER

    def __init__(self, *, max_per_poem: int = _DEFAULT_MAX_PER_POEM) -> None:
        if max_per_poem < 1:
            raise ValueError("max_per_poem must be at least 1")
        self.max_per_poem = max_per_poem

    async def rerank(
        self,
        *,
        query: str,
        evidence: list[RetrievalEvidence],
        limit: int,
    ) -> list[RetrievalEvidence]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if not evidence:
            return []

        query_content = normalize_content(query)
        query_lookup = normalize_lookup(query)
        scored = [
            _ScoredEvidence(
                evidence=item,
                score=_score_evidence(
                    query_content=query_content,
                    query_lookup=query_lookup,
                    evidence=item,
                ),
                upstream_index=index,
            )
            for index, item in enumerate(evidence)
        ]
        ranked = sorted(
            scored,
            key=lambda item: (
                -item.score,
                item.upstream_index,
                item.evidence.chunk_id,
            ),
        )
        selected = _select_diverse_candidates(
            ranked,
            limit=limit,
            max_per_poem=self.max_per_poem,
        )
        return [
            item.evidence.model_copy(
                update={
                    "score": round(item.score, 6),
                    "match_types": _append_unique(
                        item.evidence.match_types,
                        _RERANK_MATCH,
                    ),
                }
            )
            for item in selected
        ]


class RerankedRetrievalService:
    """Retrieve a wider candidate set and pass it through a reranker."""

    def __init__(
        self,
        retrieval: EvidenceRetriever,
        reranker: EvidenceReranker,
        *,
        candidate_limit: int = 30,
        strategy_name: str = RERANKED_HYBRID_STRATEGY,
    ) -> None:
        if candidate_limit < 1:
            raise ValueError("candidate_limit must be at least 1")
        self.retrieval = retrieval
        self.reranker = reranker
        self.candidate_limit = candidate_limit
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
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if limit > self.candidate_limit:
            raise ValueError("limit cannot exceed candidate_limit")

        upstream = await self.retrieval.search_evidence(
            query=query,
            limit=self.candidate_limit,
            granularities=granularities,
            author_id=author_id,
            dynasty_id=dynasty_id,
        )
        if upstream.hard_filtered:
            return RetrievalSearchResult(
                items=[],
                strategy=self.strategy_name,
                normalized_query=upstream.normalized_query,
                candidate_count=upstream.candidate_count,
                hard_filtered=True,
            )

        reranked = await self.reranker.rerank(
            query=query,
            evidence=upstream.items,
            limit=limit,
        )
        if len(reranked) > limit:
            raise RuntimeError("reranker returned more evidence than requested")
        return RetrievalSearchResult(
            items=reranked,
            strategy=self.strategy_name,
            normalized_query=upstream.normalized_query,
            candidate_count=upstream.candidate_count,
        )


def _score_evidence(
    *,
    query_content: str,
    query_lookup: str,
    evidence: RetrievalEvidence,
) -> float:
    text_content = normalize_content(evidence.text)
    exact_text_match = bool(
        query_content
        and text_content
        and query_content in text_content
    )
    text_coverage = _character_bigram_coverage(query_content, text_content)
    title_match = _metadata_match(query_lookup, evidence.title)
    author_match = bool(
        evidence.author_name
        and _metadata_match(query_lookup, evidence.author_name)
    )
    dynasty_match = bool(
        evidence.dynasty_name
        and _metadata_match(query_lookup, evidence.dynasty_name)
    )
    return min(
        1.0,
        (0.55 * exact_text_match)
        + (0.20 * text_coverage)
        + (0.12 * title_match)
        + (0.06 * author_match)
        + (0.03 * dynasty_match)
        + (0.04 * evidence.score),
    )


def _character_bigram_coverage(query: str, target: str) -> float:
    query_grams = _character_ngrams(query)
    if not query_grams:
        return 0.0
    target_grams = _character_ngrams(target)
    if not target_grams:
        return 0.0
    return len(query_grams & target_grams) / len(query_grams)


def _character_ngrams(value: str) -> set[str]:
    if not value:
        return set()
    if len(value) == 1:
        return {value}
    return {
        value[index : index + 2]
        for index in range(len(value) - 1)
    }


def _metadata_match(query_lookup: str, value: str) -> bool:
    if not query_lookup:
        return False
    for variant in _TITLE_SEPARATOR.split(value):
        normalized = normalize_lookup(variant)
        if len(normalized) < 2:
            continue
        if normalized in query_lookup or query_lookup in normalized:
            return True
    return False


def _select_diverse_candidates(
    ranked: list[_ScoredEvidence],
    *,
    limit: int,
    max_per_poem: int,
) -> list[_ScoredEvidence]:
    selected: list[_ScoredEvidence] = []
    selected_chunk_ids: set[int] = set()
    counts_by_poem: dict[int, int] = {}

    for item in ranked:
        poem_id = item.evidence.poem_id
        if counts_by_poem.get(poem_id, 0) >= max_per_poem:
            continue
        selected.append(item)
        selected_chunk_ids.add(item.evidence.chunk_id)
        counts_by_poem[poem_id] = counts_by_poem.get(poem_id, 0) + 1
        if len(selected) >= limit:
            return selected

    for item in ranked:
        if item.evidence.chunk_id in selected_chunk_ids:
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    return selected


def _append_unique(values: list[str], value: str) -> list[str]:
    if value in values:
        return list(values)
    return [*values, value]
