from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.embedding import EmbeddingProvider
from app.ai.providers.qdrant import VectorStoreError
from app.ai.providers.qwen_embedding import EmbeddingProviderError
from app.ai.providers.vector_store import VectorSearchRequest, VectorStorePort
from app.core.errors import AppError, ErrorCode
from app.models.chunk import ChunkGranularity
from app.repositories.chunks import ChunkRepository, ChunkSearchCandidate
from app.schemas.retrieval import RetrievalEvidence
from app.services.chunking import CHUNK_STRATEGY
from app.services.retrieval import RetrievalRequest, RetrievalSearchResult

DENSE_RETRIEVAL_STRATEGY = "dense-baseline-v1"
_CANDIDATE_MULTIPLIER = 5
_MAX_CANDIDATE_LIMIT = 200
_PRIMARY_TEXT_SCORE_TOLERANCE = 0.02


@dataclass(frozen=True, slots=True)
class _RankedCandidate:
    candidate: ChunkSearchCandidate
    score: float


class DenseRetrievalService:
    """Retrieve Qdrant candidates and validate them against MySQL facts.

    ``min_score`` is a cosine-similarity floor: weaker candidates are dropped at
    the retrieval layer so callers can refuse instead of handing low-relevance
    text to the generation model. Primary text chunks use a small tolerance
    because short poem and line vectors can score lower than long note vectors
    even when they contain the requested evidence.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStorePort,
        chunk_strategy: str = CHUNK_STRATEGY,
        min_score: float = 0.0,
    ) -> None:
        self.session = session
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.chunk_strategy = chunk_strategy
        self.min_score = min(max(min_score, 0.0), 1.0)
        self.chunks = ChunkRepository(session)

    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
        granularities: list[ChunkGranularity] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
    ) -> RetrievalSearchResult:
        results = await self.search_evidence_batch(
            [
                RetrievalRequest(
                    query=query,
                    limit=limit,
                    granularities=tuple(granularities or ()),
                    author_id=author_id,
                    dynasty_id=dynasty_id,
                )
            ]
        )
        return results[0]

    async def search_evidence_batch(
        self,
        requests: Sequence[RetrievalRequest],
    ) -> list[RetrievalSearchResult]:
        if not requests:
            return []

        normalized_queries: list[str] = []
        for request in requests:
            normalized_query = request.query.strip()
            if not normalized_query:
                raise AppError(
                    status_code=422,
                    code=ErrorCode.VALIDATION_ERROR,
                    message="查询内容不能为空",
                )
            normalized_queries.append(normalized_query)

        try:
            query_vectors = await self.embedding_provider.embed_documents(
                normalized_queries
            )
            if len(query_vectors) != len(requests) or any(
                not vector for vector in query_vectors
            ):
                raise EmbeddingProviderError(
                    "Embedding 返回的查询向量数量或内容无效"
                )

            hits_by_request = []
            for request, query_vector in zip(
                requests,
                query_vectors,
                strict=True,
            ):
                granularity_values = tuple(
                    granularity.value for granularity in request.granularities
                )
                hits_by_request.append(
                    await self.vector_store.search(
                        VectorSearchRequest(
                            vector=query_vector,
                            limit=_candidate_limit(request.limit),
                            granularities=granularity_values,
                            author_id=request.author_id,
                            dynasty_id=request.dynasty_id,
                            chunk_strategy=self.chunk_strategy,
                        )
                    )
                )
        except EmbeddingProviderError as exc:
            raise AppError(
                status_code=503,
                code=ErrorCode.EMBEDDING_PROVIDER_ERROR,
                message="Embedding 服务调用失败",
            ) from exc
        except VectorStoreError as exc:
            raise AppError(
                status_code=503,
                code=ErrorCode.VECTOR_STORE_ERROR,
                message="向量存储操作失败",
            ) from exc

        vector_ids = list(
            dict.fromkeys(
                hit.id
                for hits in hits_by_request
                for hit in hits
            )
        )
        candidates = await self.chunks.list_public_by_vector_ids(
            vector_ids=vector_ids,
        )
        by_vector_id = {
            candidate.vector_id: candidate
            for candidate in candidates
            if candidate.vector_id is not None
        }

        results: list[RetrievalSearchResult] = []
        for request, normalized_query, hits in zip(
            requests,
            normalized_queries,
            hits_by_request,
            strict=True,
        ):
            ranked: list[_RankedCandidate] = []
            for hit in hits:
                candidate = by_vector_id.get(hit.id)
                if candidate is None or not _candidate_matches_request(
                    candidate,
                    request,
                    chunk_strategy=self.chunk_strategy,
                ):
                    continue
                ranked.append(
                    _RankedCandidate(
                        candidate=candidate,
                        score=_clamp_similarity(hit.score),
                    )
                )

            relevant = [
                item
                for item in ranked
                if item.score >= _effective_min_score(item, self.min_score)
            ]
            results.append(
                RetrievalSearchResult(
                    items=[
                        _serialize(item.candidate, item.score)
                        for item in relevant[: request.limit]
                    ],
                    strategy=DENSE_RETRIEVAL_STRATEGY,
                    normalized_query=normalized_query,
                    candidate_count=len(relevant),
                )
            )
        return results


def _candidate_limit(limit: int) -> int:
    return min(max(limit * _CANDIDATE_MULTIPLIER, limit), _MAX_CANDIDATE_LIMIT)


def _clamp_similarity(score: float) -> float:
    return round(max(0.0, min(1.0, score)), 6)


def _effective_min_score(
    item: _RankedCandidate,
    min_score: float,
) -> float:
    if item.candidate.granularity in (
        ChunkGranularity.POEM.value,
        ChunkGranularity.LINE.value,
    ):
        return max(0.0, min_score - _PRIMARY_TEXT_SCORE_TOLERANCE)
    return min_score


def _candidate_matches_request(
    candidate: ChunkSearchCandidate,
    request: RetrievalRequest,
    *,
    chunk_strategy: str,
) -> bool:
    if candidate.chunk_strategy != chunk_strategy:
        return False
    if request.granularities and candidate.granularity not in {
        granularity.value for granularity in request.granularities
    }:
        return False
    if request.author_id is not None and candidate.author_id != request.author_id:
        return False
    if request.dynasty_id is not None and candidate.dynasty_id != request.dynasty_id:
        return False
    return True


def _serialize(
    candidate: ChunkSearchCandidate,
    score: float,
) -> RetrievalEvidence:
    return RetrievalEvidence(
        chunk_id=candidate.chunk_id,
        poem_id=candidate.poem_id,
        poem_version_id=candidate.poem_version_id,
        annotation_id=candidate.annotation_id,
        annotation_type=candidate.annotation_type,
        title=candidate.title,
        author_id=candidate.author_id,
        author_name=candidate.author_name,
        dynasty_id=candidate.dynasty_id,
        dynasty_name=candidate.dynasty_name,
        granularity=ChunkGranularity(candidate.granularity),
        chunk_index=candidate.chunk_index,
        text=candidate.text,
        line_start=candidate.line_start,
        line_end=candidate.line_end,
        chunk_strategy=candidate.chunk_strategy,
        status=candidate.status,
        score=score,
        match_types=["dense_similarity"],
        published_at=candidate.published_at,
    )
