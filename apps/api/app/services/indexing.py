from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.providers.embedding import EmbeddingProvider
from app.ai.providers.qdrant import VectorStoreError
from app.ai.providers.qwen_embedding import EmbeddingProviderError
from app.ai.providers.vector_store import VectorPoint, VectorStorePort
from app.core.errors import AppError, ErrorCode
from app.repositories.chunks import ChunkRepository, IndexableChunk
from app.services.chunk_catalog import ChunkCatalogService
from app.services.chunking import CHUNK_STRATEGY
from app.services.index_publication import lock_publication_target
from app.services.index_runs import IndexRunService

logger = logging.getLogger(__name__)

_VECTOR_NAMESPACE = uuid.UUID("e17a2c9f-5d0b-4c9c-9e1d-8a1f6f1c6c91")


@dataclass(frozen=True, slots=True)
class IndexingResult:
    run_id: int
    chunk_count: int
    embedded_count: int
    embedding_dimension: int
    vector_collection: str
    vector_ids: list[str]


class IndexingService:
    """Embed persisted chunks and atomically publish their vector metadata."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStorePort,
        created_by_id: int | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.session = session
        self.embedding_provider = embedding_provider
        self.vector_store = vector_store
        self.created_by_id = created_by_id
        self.session_factory = session_factory
        self.chunks = ChunkRepository(session)
        self.runs = IndexRunService(session, created_by_id=created_by_id)

    async def index_version(
        self,
        version_id: int,
        *,
        rebuild_chunks: bool = True,
        chunk_strategy: str = CHUNK_STRATEGY,
    ) -> IndexingResult:
        run = await self.runs.create_run(
            version_id,
            config_snapshot=self._config_snapshot(
                rebuild_chunks=rebuild_chunks,
                chunk_strategy=chunk_strategy,
            ),
            embedding_model=self.embedding_provider.model,
            embedding_dimension=self.embedding_provider.dimension,
            vector_collection=self.vector_store.collection,
            chunk_strategy=chunk_strategy,
        )

        try:
            return await self._execute_run(
                run,
                worker_id=None,
                rebuild_chunks=rebuild_chunks,
                chunk_strategy=chunk_strategy,
            )
        except Exception as exc:
            await self._fail_run(run.id, exc)
            raise self._to_app_error(exc) from exc

    async def index_claimed_run(
        self,
        run_id: int,
        *,
        worker_id: str,
        heartbeat_seconds: int = 30,
        lease_seconds: int = 900,
    ) -> IndexingResult:
        if heartbeat_seconds < 1 or heartbeat_seconds >= lease_seconds:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="heartbeat_seconds 必须大于 0 且小于 lease_seconds",
            )
        run = await self.runs.get_run(run_id)
        config = (
            run.config_snapshot
            if isinstance(run.config_snapshot, dict)
            else {}
        )
        rebuild_chunks = bool(config.get("rebuild_chunks", True))
        chunk_strategy = run.chunk_strategy

        heartbeat_task: asyncio.Task[None] | None = None
        if self.session_factory is not None:
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(
                    run_id=run_id,
                    worker_id=worker_id,
                    heartbeat_seconds=heartbeat_seconds,
                    lease_seconds=lease_seconds,
                )
            )
        try:
            return await self._execute_run(
                run,
                worker_id=worker_id,
                rebuild_chunks=rebuild_chunks,
                chunk_strategy=chunk_strategy,
                heartbeat_task=heartbeat_task,
            )
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

    async def _execute_run(
        self,
        run: Any,
        *,
        worker_id: str | None,
        rebuild_chunks: bool,
        chunk_strategy: str,
        heartbeat_task: asyncio.Task[None] | None = None,
    ) -> IndexingResult:
        upserted_ids: list[str] = []
        try:
            if worker_id is None:
                await self.runs.start(run.id)
            self._raise_if_heartbeat_failed(heartbeat_task)
            chunks = await self._prepare_chunks(
                run.poem_version_id,
                rebuild_chunks=rebuild_chunks,
                chunk_strategy=chunk_strategy,
            )
            await self.runs.mark_chunks_ready(
                run.id,
                chunk_count=len(chunks),
                worker_id=worker_id,
            )

            self._raise_if_heartbeat_failed(heartbeat_task)
            vectors = await self.embedding_provider.embed_documents(
                [chunk.text for chunk in chunks]
            )
            self._raise_if_heartbeat_failed(heartbeat_task)
            embedding_dimension = self._validate_vectors(vectors, expected_count=len(chunks))
            await self.runs.mark_embeddings_ready(
                run.id,
                embedded_count=len(vectors),
                embedding_dimension=embedding_dimension,
                worker_id=worker_id,
            )

            await self.vector_store.ensure_collection(dimension=embedding_dimension)
            await lock_publication_target(
                self.session,
                version_id=run.poem_version_id,
            )
            points = [
                self._to_vector_point(chunk, vector, index_run_id=run.id)
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
            await self.vector_store.upsert(points)
            upserted_ids = [point.id for point in points]

            published_poems = await self.chunks.mark_indexed(
                chunk_ids=[chunk.chunk_id for chunk in chunks],
                vector_ids=upserted_ids,
                embedding_model=self.embedding_provider.model,
                embedding_dimension=embedding_dimension,
                index_run_id=run.id,
                poem_version_id=run.poem_version_id,
            )
            if published_poems == 0:
                logger.warning(
                    "Index run %s finished while the poem already pointed at a "
                    "newer version; the active index pointer was left unchanged",
                    run.id,
                )
            self._raise_if_heartbeat_failed(heartbeat_task)
            await self.runs.succeed(run.id, worker_id=worker_id)
        except Exception:
            await self._compensate(upserted_ids)
            raise

        return IndexingResult(
            run_id=run.id,
            chunk_count=len(chunks),
            embedded_count=len(vectors),
            embedding_dimension=embedding_dimension,
            vector_collection=self.vector_store.collection,
            vector_ids=upserted_ids,
        )

    async def _heartbeat_loop(
        self,
        *,
        run_id: int,
        worker_id: str,
        heartbeat_seconds: int,
        lease_seconds: int,
    ) -> None:
        if self.session_factory is None:
            return
        while True:
            await asyncio.sleep(heartbeat_seconds)
            async with self.session_factory() as session:
                await IndexRunService(session).heartbeat(
                    run_id,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                )

    @staticmethod
    def _raise_if_heartbeat_failed(
        heartbeat_task: asyncio.Task[None] | None,
    ) -> None:
        if heartbeat_task is not None and heartbeat_task.done():
            heartbeat_task.result()

    async def _prepare_chunks(
        self,
        version_id: int,
        *,
        rebuild_chunks: bool,
        chunk_strategy: str,
    ) -> list[IndexableChunk]:
        if rebuild_chunks:
            await ChunkCatalogService(self.session).rebuild_version_chunks(version_id)

        chunks = await self.chunks.list_indexable_by_version(
            version_id,
            chunk_strategy=chunk_strategy,
        )
        if any(chunk.vector_id is not None for chunk in chunks):
            raise AppError(
                status_code=409,
                code=ErrorCode.CHUNKS_ALREADY_INDEXED,
                message="该版本已有向量索引，需先执行索引清理",
            )
        if not chunks:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="该版本没有可索引的 chunks",
            )
        return chunks

    def _validate_vectors(
        self,
        vectors: list[list[float]],
        *,
        expected_count: int,
    ) -> int:
        if len(vectors) != expected_count:
            raise EmbeddingProviderError("Embedding 返回数量与 chunks 数量不一致")
        if not vectors:
            raise EmbeddingProviderError("Embedding 返回空向量列表")

        dimension = self.embedding_provider.dimension or len(vectors[0])
        if dimension <= 0:
            raise EmbeddingProviderError("Embedding 返回空向量")
        for vector in vectors:
            if len(vector) != dimension:
                raise EmbeddingProviderError("Embedding 返回的向量维度不一致")
        return dimension

    def _config_snapshot(
        self,
        *,
        rebuild_chunks: bool,
        chunk_strategy: str,
    ) -> dict[str, Any]:
        return {
            "chunker": chunk_strategy,
            "provider": type(self.embedding_provider).__name__,
            "embedding_model": self.embedding_provider.model,
            "embedding_dimension": self.embedding_provider.dimension,
            "vector_store": type(self.vector_store).__name__,
            "vector_collection": self.vector_store.collection,
            "rebuild_chunks": rebuild_chunks,
        }

    @staticmethod
    def _to_vector_point(
        chunk: IndexableChunk,
        vector: list[float],
        *,
        index_run_id: int,
    ) -> VectorPoint:
        payload: dict[str, Any] = {
            "chunk_id": chunk.chunk_id,
            "poem_id": chunk.poem_id,
            "poem_version_id": chunk.poem_version_id,
            "index_run_id": index_run_id,
            "granularity": chunk.granularity,
            "chunk_index": chunk.chunk_index,
            "chunk_strategy": chunk.chunk_strategy,
            "content_hash": chunk.content_hash,
            "title": chunk.title,
        }
        optional_fields = {
            "annotation_id": chunk.annotation_id,
            "annotation_type": chunk.annotation_type,
            "author_id": chunk.author_id,
            "author_name": chunk.author_name,
            "dynasty_id": chunk.dynasty_id,
            "dynasty_name": chunk.dynasty_name,
            "line_start": chunk.line_start,
            "line_end": chunk.line_end,
        }
        payload.update(
            {
                key: value
                for key, value in optional_fields.items()
                if value is not None
            }
        )
        return VectorPoint(
            id=_vector_id(chunk),
            vector=vector,
            payload=payload,
        )

    async def _compensate(self, point_ids: list[str]) -> None:
        if not point_ids:
            return
        try:
            await self.vector_store.delete(point_ids)
        except Exception:
            logger.exception("Failed to clean up Qdrant points after indexing failure")

    async def _fail_run(self, run_id: int, exc: Exception) -> None:
        try:
            await self.session.rollback()
            await self.runs.fail(run_id, error_message=str(exc))
        except Exception:
            logger.exception("Failed to mark index run as failed", extra={"run_id": run_id})

    @staticmethod
    def _to_app_error(exc: Exception) -> AppError:
        if isinstance(exc, AppError):
            return exc
        if isinstance(exc, EmbeddingProviderError):
            return AppError(
                status_code=503,
                code=ErrorCode.EMBEDDING_PROVIDER_ERROR,
                message="Embedding 服务调用失败",
            )
        if isinstance(exc, VectorStoreError):
            return AppError(
                status_code=503,
                code=ErrorCode.VECTOR_STORE_ERROR,
                message="向量存储操作失败",
            )
        return AppError(
            status_code=500,
            code=ErrorCode.INTERNAL_ERROR,
            message="索引执行失败",
        )


def _vector_id(chunk: IndexableChunk) -> str:
    identity = (
        f"{chunk.poem_version_id}:{chunk.granularity}:{chunk.chunk_strategy}:"
        f"{chunk.chunk_index}:{chunk.content_hash}"
    )
    return str(uuid.uuid5(_VECTOR_NAMESPACE, identity))
