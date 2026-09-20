from __future__ import annotations

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
from app.services.retrieval import RetrievalSearchResult

DENSE_RETRIEVAL_STRATEGY = "dense-baseline-v1"
_CANDIDATE_MULTIPLIER = 5
_MAX_CANDIDATE_LIMIT = 200


@dataclass(frozen=True, slots=True)
class _RankedCandidate:
    candidate: ChunkSearchCandidate
    score: float


class DenseRetrievalService:
    """Retrieve Qdrant candidates and validate them against MySQL facts."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStorePort,
        chunk_strategy: str = CHUNK_STRATEGY,
    ) -> None:
        self.session = session
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.chunk_strategy = chunk_strategy
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
        normalized_query = query.strip()
        if not normalized_query:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="查询内容不能为空",
            )

        granularity_values = tuple(
            granularity.value for granularity in granularities
        ) if granularities else ()
        try:
            query_vector = await self.embedding_provider.embed_query(normalized_query)
            if not query_vector:
                raise EmbeddingProviderError("Embedding 返回空查询向量")
            hits = await self.vector_store.search(
                VectorSearchRequest(
                    vector=query_vector,
                    limit=_candidate_limit(limit),
                    granularities=granularity_values,
                    author_id=author_id,
                    dynasty_id=dynasty_id,
                    chunk_strategy=self.chunk_strategy,
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

        candidates = await self.chunks.list_public_by_vector_ids(
            vector_ids=[hit.id for hit in hits],
            granularities=list(granularity_values) or None,
            author_id=author_id,
            dynasty_id=dynasty_id,
            chunk_strategy=self.chunk_strategy,
        )
        by_vector_id = {
            candidate.vector_id: candidate
            for candidate in candidates
            if candidate.vector_id is not None
        }
        ranked: list[_RankedCandidate] = []
        for hit in hits:
            candidate = by_vector_id.get(hit.id)
            if candidate is None:
                continue
            ranked.append(
                _RankedCandidate(
                    candidate=candidate,
                    score=_clamp_similarity(hit.score),
                )
            )

        return RetrievalSearchResult(
            items=[
                _serialize(item.candidate, item.score)
                for item in ranked[:limit]
            ],
            strategy=DENSE_RETRIEVAL_STRATEGY,
            normalized_query=normalized_query,
            candidate_count=len(ranked),
        )


def _candidate_limit(limit: int) -> int:
    return min(max(limit * _CANDIDATE_MULTIPLIER, limit), _MAX_CANDIDATE_LIMIT)


def _clamp_similarity(score: float) -> float:
    return round(max(0.0, min(1.0, score)), 6)


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
