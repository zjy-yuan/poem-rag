from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from app.ai.providers.qdrant import (
    QdrantVectorStore,
    VectorStoreError,
    create_qdrant_vector_store,
)
from app.ai.providers.vector_store import VectorPoint, VectorSearchRequest
from app.core.config import Settings
from pydantic import SecretStr
from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models


class FakeQdrantClient:
    def __init__(
        self,
        *,
        exists: bool = False,
        collection_info: Any = None,
        collection_exists_error: Exception | None = None,
        get_collection_error: Exception | None = None,
        create_collection_error: Exception | None = None,
        upsert_error: Exception | None = None,
        delete_error: Exception | None = None,
        query_error: Exception | None = None,
        query_points: list[Any] | None = None,
    ) -> None:
        self.exists = exists
        self.collection_info = collection_info
        self.collection_exists_error = collection_exists_error
        self.get_collection_error = get_collection_error
        self.create_collection_error = create_collection_error
        self.upsert_error = upsert_error
        self.delete_error = delete_error
        self.query_error = query_error
        self.query_points_result = query_points or []
        self.exists_calls: list[str] = []
        self.created: list[dict[str, Any]] = []
        self.get_calls: list[str] = []
        self.upserts: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []
        self.queries: list[dict[str, Any]] = []
        self.closed = False

    async def collection_exists(self, collection_name: str) -> bool:
        self.exists_calls.append(collection_name)
        if self.collection_exists_error is not None:
            raise self.collection_exists_error
        return self.exists

    async def create_collection(self, **kwargs: Any) -> None:
        if self.create_collection_error is not None:
            raise self.create_collection_error
        self.created.append(kwargs)

    async def get_collection(self, collection_name: str) -> Any:
        self.get_calls.append(collection_name)
        if self.get_collection_error is not None:
            raise self.get_collection_error
        return self.collection_info

    async def upsert(self, **kwargs: Any) -> None:
        if self.upsert_error is not None:
            raise self.upsert_error
        self.upserts.append(kwargs)

    async def delete(self, **kwargs: Any) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deletes.append(kwargs)

    async def query_points(self, **kwargs: Any) -> Any:
        if self.query_error is not None:
            raise self.query_error
        self.queries.append(kwargs)
        return SimpleNamespace(points=self.query_points_result)

    async def close(self) -> None:
        self.closed = True


def _store(client: FakeQdrantClient, *, distance: str = "Cosine") -> QdrantVectorStore:
    return QdrantVectorStore(
        url="http://qdrant.example.test",
        collection="poem_chunks_v1",
        distance=distance,
        client=cast(AsyncQdrantClient, client),
    )


def _collection_info(*, dimension: int = 8, vector_name: str = "dense") -> Any:
    return SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors={
                    vector_name: qdrant_models.VectorParams(
                        size=dimension,
                        distance=qdrant_models.Distance.COSINE,
                    )
                }
            )
        )
    )


async def test_qdrant_creates_named_dense_collection() -> None:
    client = FakeQdrantClient(exists=False)

    await _store(client).ensure_collection(dimension=8)

    assert client.exists_calls == ["poem_chunks_v1"]
    assert client.get_calls == []
    assert len(client.created) == 1
    created = client.created[0]
    assert created["collection_name"] == "poem_chunks_v1"
    dense = created["vectors_config"]["dense"]
    assert dense.size == 8
    assert dense.distance == qdrant_models.Distance.COSINE


async def test_qdrant_accepts_existing_collection_with_same_dimension() -> None:
    client = FakeQdrantClient(exists=True, collection_info=_collection_info(dimension=8))

    await _store(client).ensure_collection(dimension=8)

    assert client.get_calls == ["poem_chunks_v1"]
    assert client.created == []


async def test_qdrant_rejects_dimension_and_vector_name_mismatch() -> None:
    dimension_client = FakeQdrantClient(
        exists=True,
        collection_info=_collection_info(dimension=7),
    )
    with pytest.raises(VectorStoreError, match="维度"):
        await _store(dimension_client).ensure_collection(dimension=8)

    name_client = FakeQdrantClient(
        exists=True,
        collection_info=_collection_info(vector_name="other"),
    )
    with pytest.raises(VectorStoreError, match="dense"):
        await _store(name_client).ensure_collection(dimension=8)


async def test_qdrant_upserts_point_with_named_vector_and_payload() -> None:
    client = FakeQdrantClient()
    store = _store(client)
    point = VectorPoint(
        id="point-1",
        vector=[0.1, 0.2, 0.3],
        payload={"chunk_id": 7, "content_hash": "hash-7"},
    )

    await store.upsert([point])

    assert len(client.upserts) == 1
    call = client.upserts[0]
    assert call["collection_name"] == "poem_chunks_v1"
    assert call["wait"] is True
    assert len(call["points"]) == 1
    stored = call["points"][0]
    assert stored.id == "point-1"
    assert stored.vector == {"dense": [0.1, 0.2, 0.3]}
    assert stored.payload == {"chunk_id": 7, "content_hash": "hash-7"}


async def test_qdrant_deletes_points_and_skips_empty_calls() -> None:
    client = FakeQdrantClient()
    store = _store(client)

    await store.delete([])
    await store.delete(["point-1", "point-2"])

    assert len(client.deletes) == 1
    call = client.deletes[0]
    assert call["collection_name"] == "poem_chunks_v1"
    assert call["wait"] is True
    assert call["points_selector"].points == ["point-1", "point-2"]


async def test_qdrant_searches_named_vector_with_filters() -> None:
    client = FakeQdrantClient(
        query_points=[
            SimpleNamespace(
                id="point-1",
                score=0.91,
                payload={"chunk_id": 7},
            )
        ]
    )
    store = _store(client)

    hits = await store.search(
        VectorSearchRequest(
            vector=[0.1, 0.2, 0.3],
            limit=5,
            granularities=("line", "poem"),
            author_id=3,
            dynasty_id=4,
            chunk_strategy="structural-v1",
        )
    )

    assert len(hits) == 1
    assert hits[0].id == "point-1"
    assert hits[0].score == 0.91
    assert hits[0].payload == {"chunk_id": 7}
    assert len(client.queries) == 1
    call = client.queries[0]
    assert call["collection_name"] == "poem_chunks_v1"
    assert call["query"] == [0.1, 0.2, 0.3]
    assert call["using"] == "dense"
    assert call["limit"] == 5
    assert call["with_payload"] is True
    assert call["with_vectors"] is False
    assert call["query_filter"] is not None
    conditions = call["query_filter"].must
    assert len(conditions) == 4
    assert conditions[0].key == "granularity"
    assert conditions[0].match.any == ["line", "poem"]
    assert conditions[1].key == "author_id"
    assert conditions[1].match.value == 3
    assert conditions[2].key == "dynasty_id"
    assert conditions[2].match.value == 4
    assert conditions[3].key == "chunk_strategy"
    assert conditions[3].match.value == "structural-v1"


async def test_qdrant_skips_empty_filter_and_wraps_query_failures() -> None:
    client = FakeQdrantClient()
    await _store(client).search(
        VectorSearchRequest(vector=[1.0, 0.0], limit=1)
    )
    assert client.queries[0]["query_filter"] is None

    query_client = FakeQdrantClient(query_error=RuntimeError("query failed"))
    with pytest.raises(VectorStoreError, match="检索"):
        await _store(query_client).search(
            VectorSearchRequest(vector=[1.0, 0.0], limit=1)
        )


async def test_qdrant_wraps_sdk_failures() -> None:
    collection_client = FakeQdrantClient(
        collection_exists_error=RuntimeError("collection unavailable")
    )
    with pytest.raises(VectorStoreError, match="检查或创建"):
        await _store(collection_client).ensure_collection(dimension=8)

    upsert_client = FakeQdrantClient(upsert_error=RuntimeError("write failed"))
    with pytest.raises(VectorStoreError, match="写入"):
        await _store(upsert_client).upsert([VectorPoint(id="point-1", vector=[1.0])])

    delete_client = FakeQdrantClient(delete_error=RuntimeError("delete failed"))
    with pytest.raises(VectorStoreError, match="删除"):
        await _store(delete_client).delete(["point-1"])


def test_create_qdrant_vector_store_uses_settings() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite://",
        qdrant_url="http://configured-qdrant:6333",
        qdrant_api_key=SecretStr("configured-key"),
        qdrant_collection="configured-collection",
        qdrant_distance="Dot",
        qdrant_timeout_seconds=12.5,
    )

    store = create_qdrant_vector_store(settings)

    assert store.collection == "configured-collection"


def test_create_qdrant_vector_store_requires_url() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite://",
        qdrant_url=None,
    )

    with pytest.raises(VectorStoreError, match="URL"):
        create_qdrant_vector_store(settings)
