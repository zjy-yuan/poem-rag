from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.core.errors import AppError, ErrorCode
from app.models.chunk import ChunkGranularity
from app.schemas.retrieval import RetrievalEvidence
from app.services.retrieval import RetrievalSearchResult

HYBRID_RETRIEVAL_STRATEGY = "hybrid-rrf-v1"
RRF_RANK_CONSTANT = 60

_BRANCH_COUNT = 2
_CANDIDATE_MULTIPLIER = 5
_MAX_CANDIDATE_LIMIT = 200
_MAX_RRF_SCORE = _BRANCH_COUNT / (RRF_RANK_CONSTANT + 1)
_LEXICAL_MATCH = "lexical_match"
_DENSE_MATCH = "dense_similarity"
_RRF_MATCH = "rrf_fusion"


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


@dataclass(slots=True)
class _FusedCandidate:
    """Keep source ranks and scores while building the RRF result."""

    evidence: RetrievalEvidence
    rrf_score: float = 0.0
    lexical_rank: int | None = None
    lexical_score: float | None = None
    lexical_match_types: list[str] = field(default_factory=list)
    dense_rank: int | None = None
    dense_score: float | None = None


class HybridRetrievalService:
    """Fuse lexical and dense candidates with Reciprocal Rank Fusion."""

    def __init__(
        self,
        lexical: RetrievalBranch,
        dense: RetrievalBranch,
    ) -> None:
        self.lexical = lexical
        self.dense = dense

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

        branch_limit = _candidate_limit(limit)
        lexical_result = await self.lexical.search_evidence(
            query=query,
            limit=branch_limit,
            granularities=granularities,
            author_id=author_id,
            dynasty_id=dynasty_id,
        )
        dense_result = await self.dense.search_evidence(
            query=query,
            limit=branch_limit,
            granularities=granularities,
            author_id=author_id,
            dynasty_id=dynasty_id,
        )

        fused: dict[int, _FusedCandidate] = {}
        _add_branch(fused, lexical_result.items, source="lexical")
        _add_branch(fused, dense_result.items, source="dense")
        ranked = sorted(
            fused.values(),
            key=lambda item: (
                -item.rrf_score,
                _best_source_rank(item),
                item.evidence.chunk_id,
            ),
        )

        return RetrievalSearchResult(
            items=[_serialize(item) for item in ranked[:limit]],
            strategy=HYBRID_RETRIEVAL_STRATEGY,
            normalized_query=(
                lexical_result.normalized_query or dense_result.normalized_query
            ),
            candidate_count=len(ranked),
        )


def _candidate_limit(limit: int) -> int:
    return min(max(limit * _CANDIDATE_MULTIPLIER, limit), _MAX_CANDIDATE_LIMIT)


def _add_branch(
    fused: dict[int, _FusedCandidate],
    items: list[RetrievalEvidence],
    *,
    source: str,
) -> None:
    for rank, evidence in enumerate(items, start=1):
        candidate = fused.get(evidence.chunk_id)
        if candidate is None:
            candidate = _FusedCandidate(evidence=evidence)
            fused[evidence.chunk_id] = candidate

        if source == "lexical":
            if candidate.lexical_rank is not None:
                continue
            candidate.lexical_rank = rank
            candidate.lexical_score = evidence.score
            candidate.lexical_match_types = list(evidence.match_types)
        else:
            if candidate.dense_rank is not None:
                continue
            candidate.dense_rank = rank
            candidate.dense_score = evidence.score

        candidate.rrf_score += 1.0 / (RRF_RANK_CONSTANT + rank)


def _best_source_rank(candidate: _FusedCandidate) -> int:
    ranks = [
        rank
        for rank in (candidate.lexical_rank, candidate.dense_rank)
        if rank is not None
    ]
    return min(ranks)


def _serialize(candidate: _FusedCandidate) -> RetrievalEvidence:
    match_types: list[str] = []
    if candidate.lexical_rank is not None:
        _append_unique(match_types, _LEXICAL_MATCH)
        for match_type in candidate.lexical_match_types:
            _append_unique(match_types, match_type)
    if candidate.dense_rank is not None:
        _append_unique(match_types, _DENSE_MATCH)
    _append_unique(match_types, _RRF_MATCH)

    return candidate.evidence.model_copy(
        update={
            "score": round(min(1.0, candidate.rrf_score / _MAX_RRF_SCORE), 6),
            "match_types": match_types,
        }
    )


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
