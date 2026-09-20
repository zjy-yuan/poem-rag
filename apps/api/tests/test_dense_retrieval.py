from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from app.ai.providers.qdrant import VectorStoreError
from app.ai.providers.qwen_embedding import EmbeddingProviderError
from app.ai.providers.vector_store import (
    VectorPoint,
    VectorSearchHit,
    VectorSearchRequest,
)
from app.core.errors import AppError, ErrorCode
from app.core.text import normalize_content, sha256_text
from app.models.annotation import AnnotationStatus, AnnotationType, PoemAnnotation
from app.models.chunk import ChunkGranularity, ChunkStatus, PoemChunk
from app.services.dense_retrieval import DenseRetrievalService
from fastapi.testclient import TestClient
from test_catalog import _admin_client
from test_rag_corpus import _load_versions


@dataclass(frozen=True, slots=True)
class FakeEmbeddingProvider:
    model: str = "fake-embedding"
    dimension: int | None = 3

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0, 0.0]


class FakeVectorStore:
    def __init__(self, hits: list[VectorSearchHit]) -> None:
        self._collection = "test-poem-chunks"
        self.hits = hits
        self.requests: list[VectorSearchRequest] = []

    @property
    def collection(self) -> str:
        return self._collection

    async def ensure_collection(self, *, dimension: int) -> None:
        del dimension

    async def upsert(self, points: list[VectorPoint]) -> None:
        del points

    async def delete(self, point_ids: list[str]) -> None:
        del point_ids

    async def search(self, request: VectorSearchRequest) -> list[VectorSearchHit]:
        self.requests.append(request)
        return self.hits


class FailingEmbeddingProvider(FakeEmbeddingProvider):
    async def embed_query(self, text: str) -> list[float]:
        del text
        raise EmbeddingProviderError("provider unavailable")


class FailingVectorStore(FakeVectorStore):
    async def search(self, request: VectorSearchRequest) -> list[VectorSearchHit]:
        del request
        raise VectorStoreError("vector store unavailable")


def _create_poem(
    client: TestClient,
    *,
    headers: dict[str, str],
    title: str,
    content: str,
    published: bool = True,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={"title": title, "content": content},
    )
    assert response.status_code == 201
    poem = response.json()["data"]
    if published:
        publish_response = client.post(
            f"/api/v1/admin/poems/{poem['id']}/publish",
            headers=headers,
        )
        assert publish_response.status_code == 200
    return poem


def _prepare_indexed_chunks(
    client: TestClient,
    poem_id: int,
) -> tuple[list[PoemChunk], list[str]]:
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem_id)

    async def create() -> tuple[list[PoemChunk], list[str]]:
        from app.services.chunk_catalog import ChunkCatalogService

        async with session_factory() as session:
            chunks = await ChunkCatalogService(session).rebuild_version_chunks(
                versions[0].id
            )
            vector_ids: list[str] = []
            for index, chunk in enumerate(chunks):
                vector_id = f"vector-{poem_id}-{index}"
                chunk.vector_id = vector_id
                chunk.embedding_model = "fake-embedding"
                chunk.embedding_dimension = 3
                chunk.status = ChunkStatus.READY.value
                vector_ids.append(vector_id)
            await session.commit()
            return chunks, vector_ids

    return portal.call(create)


def test_dense_retrieval_returns_mysql_evidence_in_qdrant_order(
    client: TestClient,
) -> None:
    headers, _ = _admin_client(client)
    poem = _create_poem(
        client,
        headers=headers,
        title="Quiet Night",
        content="Moonlight before my bed.\nI lower my head and think of home.",
    )
    chunks, vector_ids = _prepare_indexed_chunks(client, poem["id"])
    store = FakeVectorStore(
        [
            VectorSearchHit(id=vector_ids[1], score=0.91),
            VectorSearchHit(id=vector_ids[0], score=0.83),
        ]
    )
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def search() -> Any:
        async with session_factory() as session:
            return await DenseRetrievalService(
                session,
                embedding_provider=FakeEmbeddingProvider(),
                vector_store=store,
            ).search_evidence(query="moon", limit=2)

    result = portal.call(search)

    assert result.strategy == "dense-baseline-v1"
    assert result.normalized_query == "moon"
    assert result.candidate_count == 2
    assert [item.chunk_id for item in result.items] == [
        chunks[1].id,
        chunks[0].id,
    ]
    assert [item.score for item in result.items] == [0.91, 0.83]
    assert all(item.match_types == ["dense_similarity"] for item in result.items)
    assert store.requests[0].limit == 10
    assert store.requests[0].chunk_strategy == "structural-v1"


def test_dense_retrieval_drops_candidates_below_min_score(
    client: TestClient,
) -> None:
    headers, _ = _admin_client(client)
    poem = _create_poem(
        client,
        headers=headers,
        title="Scored",
        content="Moonlight line.\nAnother line.",
    )
    chunks, vector_ids = _prepare_indexed_chunks(client, poem["id"])
    store = FakeVectorStore(
        [
            VectorSearchHit(id=vector_ids[1], score=0.91),
            VectorSearchHit(id=vector_ids[0], score=0.21),
        ]
    )
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def search(min_score: float) -> Any:
        async with session_factory() as session:
            return await DenseRetrievalService(
                session,
                embedding_provider=FakeEmbeddingProvider(),
                vector_store=store,
                min_score=min_score,
            ).search_evidence(query="moon", limit=5)

    kept = portal.call(search, 0.5)
    assert [item.chunk_id for item in kept.items] == [chunks[1].id]
    assert kept.candidate_count == 1

    empty = portal.call(search, 0.95)
    assert empty.items == []
    assert empty.candidate_count == 0

    unfiltered = portal.call(search, 0.0)
    assert [item.chunk_id for item in unfiltered.items] == [
        chunks[1].id,
        chunks[0].id,
    ]
    assert unfiltered.candidate_count == 2


def test_dense_retrieval_drops_stale_and_invisible_qdrant_hits(
    client: TestClient,
) -> None:
    headers, _ = _admin_client(client)
    visible = _create_poem(
        client,
        headers=headers,
        title="Visible",
        content="Visible line.",
    )
    draft = _create_poem(
        client,
        headers=headers,
        title="Draft",
        content="Draft line.",
        published=False,
    )
    visible_chunks, visible_vector_ids = _prepare_indexed_chunks(client, visible["id"])
    _, draft_vector_ids = _prepare_indexed_chunks(client, draft["id"])
    store = FakeVectorStore(
        [
            VectorSearchHit(id=draft_vector_ids[0], score=0.99),
            VectorSearchHit(id="missing-vector", score=0.98),
            VectorSearchHit(id=visible_vector_ids[0], score=0.75),
        ]
    )
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def search() -> Any:
        async with session_factory() as session:
            return await DenseRetrievalService(
                session,
                embedding_provider=FakeEmbeddingProvider(),
                vector_store=store,
            ).search_evidence(query="line", limit=5)

    result = portal.call(search)

    assert [item.chunk_id for item in result.items] == [visible_chunks[0].id]
    assert result.candidate_count == 1


def test_dense_retrieval_filters_draft_note_annotations(
    client: TestClient,
) -> None:
    headers, _ = _admin_client(client)
    poem = _create_poem(
        client,
        headers=headers,
        title="Annotated",
        content="Current line.",
    )
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def add_draft_note() -> tuple[int, str]:
        async with session_factory() as session:
            annotation = PoemAnnotation(
                poem_version_id=versions[0].id,
                annotation_type=AnnotationType.APPRECIATION.value,
                content="Draft appreciation.",
                normalized_content=normalize_content("Draft appreciation."),
                status=AnnotationStatus.DRAFT.value,
                content_hash=sha256_text("Draft appreciation."),
            )
            session.add(annotation)
            await session.flush()
            chunk = PoemChunk(
                poem_id=poem["id"],
                poem_version_id=versions[0].id,
                annotation_id=annotation.id,
                granularity=ChunkGranularity.NOTE.value,
                chunk_index=0,
                text=annotation.content,
                normalized_text=annotation.normalized_content,
                content_hash=annotation.content_hash,
                chunk_strategy="structural-v1",
                status=ChunkStatus.READY.value,
                vector_id="draft-note-vector",
                embedding_model="fake-embedding",
                embedding_dimension=3,
            )
            session.add(chunk)
            await session.commit()
            return chunk.id, "draft-note-vector"

    draft_chunk_id, draft_vector_id = portal.call(add_draft_note)
    assert draft_chunk_id > 0
    store = FakeVectorStore(
        [VectorSearchHit(id=draft_vector_id, score=0.99)]
    )

    async def search() -> Any:
        async with session_factory() as session:
            return await DenseRetrievalService(
                session,
                embedding_provider=FakeEmbeddingProvider(),
                vector_store=store,
            ).search_evidence(
                query="appreciation",
                limit=5,
                granularities=[ChunkGranularity.NOTE],
            )

    result = portal.call(search)
    assert result.items == []


def test_dense_retrieval_drops_old_version_vectors_after_update(
    client: TestClient,
) -> None:
    headers, _ = _admin_client(client)
    poem = _create_poem(
        client,
        headers=headers,
        title="Versioned",
        content="Old version line.",
    )
    _, old_vector_ids = _prepare_indexed_chunks(client, poem["id"])
    store = FakeVectorStore(
        [VectorSearchHit(id=old_vector_ids[0], score=0.99)]
    )
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    update_response = client.patch(
        f"/api/v1/admin/poems/{poem['id']}",
        headers=headers,
        json={"content": "New version line.", "version_no": 1},
    )
    assert update_response.status_code == 200

    result = portal.call(_search_visible, session_factory, store)

    assert result == []


async def _search_visible(
    session_factory: Any,
    store: FakeVectorStore,
) -> list[Any]:
    async with session_factory() as session:
        result = await DenseRetrievalService(
            session,
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=store,
        ).search_evidence(query="line", limit=5)
        return result.items


def test_dense_retrieval_rejects_blank_query_and_wraps_provider_errors(
    client: TestClient,
) -> None:
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def search(provider: Any, store: Any, query: str) -> None:
        async with session_factory() as session:
            await DenseRetrievalService(
                session,
                embedding_provider=provider,
                vector_store=store,
            ).search_evidence(query=query, limit=5)

    with pytest.raises(AppError) as blank:
        portal.call(search, FakeEmbeddingProvider(), FakeVectorStore([]), "   ")
    assert blank.value.code == ErrorCode.VALIDATION_ERROR

    with pytest.raises(AppError) as embedding_error:
        portal.call(
            search,
            FailingEmbeddingProvider(),
            FakeVectorStore([]),
            "moon",
        )
    assert embedding_error.value.code == ErrorCode.EMBEDDING_PROVIDER_ERROR

    with pytest.raises(AppError) as vector_error:
        portal.call(
            search,
            FakeEmbeddingProvider(),
            FailingVectorStore([]),
            "moon",
        )
    assert vector_error.value.code == ErrorCode.VECTOR_STORE_ERROR
