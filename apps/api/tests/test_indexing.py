from __future__ import annotations

from typing import Any

import pytest
from app.ai.providers.qdrant import VectorStoreError
from app.ai.providers.qwen_embedding import EmbeddingProviderError
from app.ai.providers.vector_store import VectorPoint
from app.core.errors import AppError, ErrorCode
from app.models.chunk import ChunkStatus, PoemChunk
from app.models.index_run import IndexRunStage, IndexRunStatus, PoemIndexRun
from app.repositories.chunks import ChunkRepository
from app.services.indexing import IndexingService
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_catalog import _admin_client
from test_rag_corpus import _load_versions


class FakeEmbeddingProvider:
    def __init__(self, *, dimension: int = 8, fail: bool = False) -> None:
        self._dimension = dimension
        self.fail = fail
        self.calls: list[list[str]] = []

    @property
    def model(self) -> str:
        return "fake-embedding"

    @property
    def dimension(self) -> int | None:
        return self._dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self.fail:
            raise EmbeddingProviderError("provider unavailable", retryable=True)
        return [[float(index + 1)] * self._dimension for index, _ in enumerate(texts)]

    async def embed_query(self, text: str) -> list[float]:
        return [1.0] * self._dimension


class FakeVectorStore:
    def __init__(self, *, fail_upsert: bool = False) -> None:
        self._collection = "test-poem-chunks"
        self.fail_upsert = fail_upsert
        self.ensured_dimensions: list[int] = []
        self.upserts: list[list[VectorPoint]] = []
        self.deleted: list[list[str]] = []

    @property
    def collection(self) -> str:
        return self._collection

    async def ensure_collection(self, *, dimension: int) -> None:
        self.ensured_dimensions.append(dimension)

    async def upsert(self, points: list[VectorPoint]) -> None:
        if self.fail_upsert:
            raise VectorStoreError("qdrant unavailable")
        self.upserts.append(points)

    async def delete(self, point_ids: list[str]) -> None:
        self.deleted.append(point_ids)


def _create_poem(client: TestClient) -> dict[str, Any]:
    headers, _ = _admin_client(client)
    return client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={
            "title": "Indexed Poem",
            "content": "First line.\nSecond line.",
        },
    ).json()["data"]


def test_indexing_service_embeds_upserts_and_marks_chunks_ready(
    client: TestClient,
) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])
    provider = FakeEmbeddingProvider()
    store = FakeVectorStore()

    async def index() -> tuple[int, int, int, int, str, list[str]]:
        async with session_factory() as session:
            result = await IndexingService(
                session,
                embedding_provider=provider,
                vector_store=store,
            ).index_version(versions[0].id)
            return (
                result.run_id,
                result.chunk_count,
                result.embedded_count,
                result.embedding_dimension,
                result.vector_collection,
                result.vector_ids,
            )

    run_id, chunk_count, embedded_count, dimension, collection, vector_ids = portal.call(
        index
    )

    async def load_state() -> tuple[PoemIndexRun, list[PoemChunk]]:
        async with session_factory() as session:
            run = await session.get(PoemIndexRun, run_id)
            assert run is not None
            chunks = list(
                (
                    await session.execute(
                        select(PoemChunk)
                        .where(PoemChunk.poem_version_id == versions[0].id)
                        .order_by(PoemChunk.id)
                    )
                )
                .scalars()
                .all()
            )
            return run, chunks

    run, chunks = portal.call(load_state)

    assert run.status == IndexRunStatus.SUCCEEDED.value
    assert run.stage == IndexRunStage.UPSERT.value
    assert run.embedding_dimension == 8
    assert run.vector_collection == collection
    assert chunk_count == 3
    assert embedded_count == 3
    assert len(vector_ids) == 3
    assert len(set(vector_ids)) == 3
    assert store.ensured_dimensions == [8]
    assert len(store.upserts) == 1
    assert all(chunk.status == ChunkStatus.READY.value for chunk in chunks)
    assert all(chunk.vector_id in vector_ids for chunk in chunks)
    assert all(chunk.embedding_model == "fake-embedding" for chunk in chunks)
    assert all(chunk.embedding_dimension == 8 for chunk in chunks)


def test_indexing_service_rejects_inconsistent_vector_dimension(
    client: TestClient,
) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])
    provider = FakeEmbeddingProvider(dimension=8)
    store = FakeVectorStore()

    async def embed_documents(texts: list[str]) -> list[list[float]]:
        return [[1.0] * 7 for _ in texts]

    provider.embed_documents = embed_documents  # type: ignore[method-assign]

    async def index() -> None:
        async with session_factory() as session:
            await IndexingService(
                session,
                embedding_provider=provider,
                vector_store=store,
            ).index_version(versions[0].id)

    with pytest.raises(AppError) as exc_info:
        portal.call(index)

    assert exc_info.value.code == ErrorCode.EMBEDDING_PROVIDER_ERROR
    assert store.upserts == []
    assert store.deleted == []

    async def load_run() -> PoemIndexRun | None:
        async with session_factory() as session:
            return await session.scalar(select(PoemIndexRun))

    run = portal.call(load_run)
    assert run is not None
    assert run.status == IndexRunStatus.FAILED.value


def test_indexing_service_compensates_after_upsert_failure(
    client: TestClient,
) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])
    provider = FakeEmbeddingProvider()
    store = FakeVectorStore(fail_upsert=True)

    async def index() -> None:
        async with session_factory() as session:
            await IndexingService(
                session,
                embedding_provider=provider,
                vector_store=store,
            ).index_version(versions[0].id)

    with pytest.raises(AppError) as exc_info:
        portal.call(index)

    assert exc_info.value.code == ErrorCode.VECTOR_STORE_ERROR
    assert store.upserts == []
    assert store.deleted == []

    async def load_state() -> tuple[PoemIndexRun | None, list[PoemChunk]]:
        async with session_factory() as session:
            run = await session.scalar(select(PoemIndexRun))
            chunks = list(
                (
                    await session.execute(
                        select(PoemChunk).where(
                            PoemChunk.poem_version_id == versions[0].id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return run, chunks

    run, chunks = portal.call(load_state)
    assert run is not None
    assert run.status == IndexRunStatus.FAILED.value
    assert all(chunk.status == ChunkStatus.PENDING.value for chunk in chunks)
    assert all(chunk.vector_id is None for chunk in chunks)


def test_indexing_service_rejects_already_indexed_chunks(
    client: TestClient,
) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])
    provider = FakeEmbeddingProvider()
    store = FakeVectorStore()

    async def seed_vector_id() -> None:
        async with session_factory() as session:
            service = IndexingService(
                session,
                embedding_provider=provider,
                vector_store=store,
            )
            await service._prepare_chunks(
                versions[0].id,
                rebuild_chunks=True,
                chunk_strategy="structural-v1",
            )
            chunk = await session.scalar(
                select(PoemChunk).where(PoemChunk.poem_version_id == versions[0].id)
            )
            assert chunk is not None
            chunk.vector_id = "existing-vector"
            await session.commit()

    portal.call(seed_vector_id)

    async def index() -> None:
        async with session_factory() as session:
            await IndexingService(
                session,
                embedding_provider=provider,
                vector_store=store,
            ).index_version(versions[0].id, rebuild_chunks=False)

    with pytest.raises(AppError) as exc_info:
        portal.call(index)

    assert exc_info.value.code == ErrorCode.CHUNKS_ALREADY_INDEXED


def test_indexing_service_compensates_when_database_write_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])
    provider = FakeEmbeddingProvider()
    store = FakeVectorStore()

    async def fail_mark_indexed(
        self: ChunkRepository,
        *,
        chunk_ids: list[int],
        vector_ids: list[str],
        embedding_model: str,
        embedding_dimension: int,
    ) -> None:
        raise RuntimeError("database write failed")

    monkeypatch.setattr(ChunkRepository, "mark_indexed", fail_mark_indexed)

    async def index() -> None:
        async with session_factory() as session:
            await IndexingService(
                session,
                embedding_provider=provider,
                vector_store=store,
            ).index_version(versions[0].id)

    with pytest.raises(AppError) as exc_info:
        portal.call(index)

    assert exc_info.value.code == ErrorCode.INTERNAL_ERROR
    assert len(store.upserts) == 1
    upserted_ids = [point.id for point in store.upserts[0]]
    assert store.deleted == [upserted_ids]

    async def load_state() -> tuple[PoemIndexRun | None, list[PoemChunk]]:
        async with session_factory() as session:
            run = await session.scalar(select(PoemIndexRun))
            chunks = list(
                (
                    await session.execute(
                        select(PoemChunk).where(
                            PoemChunk.poem_version_id == versions[0].id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return run, chunks

    run, chunks = portal.call(load_state)
    assert run is not None
    assert run.status == IndexRunStatus.FAILED.value
    assert all(chunk.status == ChunkStatus.PENDING.value for chunk in chunks)
    assert all(chunk.vector_id is None for chunk in chunks)
