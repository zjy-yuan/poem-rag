from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from app.ai.providers.embedding_cache import (
    CachedEmbeddingProvider,
    RedisEmbeddingCache,
    build_embedding_cache_key,
)
from app.core.config import Settings
from app.services import chat as chat_service
from pydantic import SecretStr, ValidationError


class FakeEmbeddingProvider:
    def __init__(
        self,
        *,
        model: str = "embedding-test",
        dimension: int | None = 2,
        document_vectors: dict[str, list[float]],
        query_vector: list[float],
        error: Exception | None = None,
    ) -> None:
        self.model = model
        self.dimension = dimension
        self.document_vectors = document_vectors
        self.query_vector = query_vector
        self.error = error
        self.document_calls: list[list[str]] = []
        self.query_calls: list[str] = []
        self.closed = False

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls.append(list(texts))
        if self.error is not None:
            raise self.error
        return [list(self.document_vectors[text]) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        if self.error is not None:
            raise self.error
        return list(self.query_vector)

    async def aclose(self) -> None:
        self.closed = True


class MemoryEmbeddingCache:
    def __init__(
        self,
        values: dict[str, str] | None = None,
        *,
        fail_reads: bool = False,
        fail_writes: bool = False,
        fail_reads_with: BaseException | None = None,
    ) -> None:
        self.values = dict(values or {})
        self.fail_reads = fail_reads
        self.fail_writes = fail_writes
        self.fail_reads_with = fail_reads_with
        self.get_calls: list[list[str]] = []
        self.set_calls: list[dict[str, str]] = []
        self.closed = False

    async def get_many(self, keys: list[str]) -> list[str | None]:
        self.get_calls.append(list(keys))
        if self.fail_reads_with is not None:
            raise self.fail_reads_with
        if self.fail_reads:
            raise RuntimeError("redis unavailable")
        return [self.values.get(key) for key in keys]

    async def set_many(
        self,
        values: dict[str, str],
        *,
        ttl_seconds: int,
    ) -> None:
        assert ttl_seconds > 0
        self.set_calls.append(dict(values))
        if self.fail_writes:
            raise RuntimeError("redis unavailable")
        self.values.update(values)

    async def aclose(self) -> None:
        self.closed = True


async def test_embed_documents_deduplicates_and_restores_order() -> None:
    provider = FakeEmbeddingProvider(
        document_vectors={
            "明月": [1.0, 0.0],
            "杨柳": [0.0, 1.0],
        },
        query_vector=[1.0, 0.0],
    )
    cache = MemoryEmbeddingCache()
    cached = CachedEmbeddingProvider(
        provider,
        cache=cache,
        ttl_seconds=60,
    )

    vectors = await cached.embed_documents(["明月", "杨柳", "明月"])

    assert provider.document_calls == [["明月", "杨柳"]]
    assert vectors == [[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]
    assert cached.stats.hits == 0
    assert cached.stats.misses == 2
    assert cached.stats.writes == 2


async def test_second_embedding_call_reuses_cached_vectors() -> None:
    provider = FakeEmbeddingProvider(
        document_vectors={"明月": [1.0, 0.0]},
        query_vector=[1.0, 0.0],
    )
    cached = CachedEmbeddingProvider(
        provider,
        cache=MemoryEmbeddingCache(),
        ttl_seconds=60,
    )

    first = await cached.embed_documents(["明月"])
    second = await cached.embed_documents(["明月"])

    assert first == second == [[1.0, 0.0]]
    assert provider.document_calls == [["明月"]]
    assert cached.stats.hits == 1
    assert cached.stats.misses == 1


async def test_document_and_query_cache_keys_are_isolated() -> None:
    provider = FakeEmbeddingProvider(
        document_vectors={"明月": [1.0, 0.0]},
        query_vector=[0.0, 1.0],
    )
    cached = CachedEmbeddingProvider(
        provider,
        cache=MemoryEmbeddingCache(),
        ttl_seconds=60,
    )

    document_vector = await cached.embed_documents(["明月"])
    query_vector = await cached.embed_query("明月")

    assert document_vector == [[1.0, 0.0]]
    assert query_vector == [0.0, 1.0]
    assert provider.document_calls == [["明月"]]
    assert provider.query_calls == ["明月"]


async def test_model_and_dimension_changes_isolate_cache_entries() -> None:
    provider = FakeEmbeddingProvider(
        model="embedding-v1",
        dimension=2,
        document_vectors={"明月": [1.0, 0.0]},
        query_vector=[1.0, 0.0],
    )
    cache = MemoryEmbeddingCache()
    cached = CachedEmbeddingProvider(
        provider,
        cache=cache,
        ttl_seconds=60,
    )

    await cached.embed_documents(["明月"])
    provider.model = "embedding-v2"
    provider.dimension = 3
    provider.document_vectors["明月"] = [1.0, 0.0, 0.0]
    await cached.embed_documents(["明月"])

    assert provider.document_calls == [["明月"], ["明月"]]
    assert len(cache.values) == 2


@pytest.mark.parametrize(
    "cached_value",
    [
        "not-json",
        json.dumps({"version": 1, "vector": []}),
        json.dumps({"version": 1, "vector": [1.0, float("nan")]}),
        json.dumps({"version": 1, "vector": [1.0, 0.0, 0.0]}),
    ],
)
async def test_corrupt_cached_vector_is_treated_as_miss(
    cached_value: str,
) -> None:
    provider = FakeEmbeddingProvider(
        document_vectors={"明月": [1.0, 0.0]},
        query_vector=[1.0, 0.0],
    )
    key = build_embedding_cache_key(
        "明月",
        scope="documents",
        model=provider.model,
        dimension=provider.dimension,
    )
    cache = MemoryEmbeddingCache({key: cached_value})
    cached = CachedEmbeddingProvider(
        provider,
        cache=cache,
        ttl_seconds=60,
    )

    assert await cached.embed_documents(["明月"]) == [[1.0, 0.0]]
    assert provider.document_calls == [["明月"]]
    assert cached.stats.hits == 0
    assert cached.stats.misses == 1
    assert cached.stats.writes == 1
    assert json.loads(cache.values[key])["vector"] == [1.0, 0.0]


async def test_cache_read_failure_falls_back_to_provider() -> None:
    provider = FakeEmbeddingProvider(
        document_vectors={"明月": [1.0, 0.0]},
        query_vector=[1.0, 0.0],
    )
    cached = CachedEmbeddingProvider(
        provider,
        cache=MemoryEmbeddingCache(fail_reads=True),
        ttl_seconds=60,
    )

    assert await cached.embed_documents(["明月"]) == [[1.0, 0.0]]
    assert provider.document_calls == [["明月"]]
    assert cached.stats.errors == 1


async def test_cache_write_failure_does_not_change_provider_result() -> None:
    provider = FakeEmbeddingProvider(
        document_vectors={"明月": [1.0, 0.0]},
        query_vector=[1.0, 0.0],
    )
    cached = CachedEmbeddingProvider(
        provider,
        cache=MemoryEmbeddingCache(fail_writes=True),
        ttl_seconds=60,
    )

    assert await cached.embed_query("明月") == [1.0, 0.0]
    assert provider.query_calls == ["明月"]
    assert cached.stats.errors == 1
    assert cached.stats.writes == 0


async def test_provider_error_is_not_cached() -> None:
    provider_error = ValueError("provider failed")
    cache = MemoryEmbeddingCache()
    cached = CachedEmbeddingProvider(
        FakeEmbeddingProvider(
            document_vectors={},
            query_vector=[],
            error=provider_error,
        ),
        cache=cache,
        ttl_seconds=60,
    )

    with pytest.raises(ValueError, match="provider failed"):
        await cached.embed_query("明月")

    assert cache.set_calls == []


async def test_cancelled_cache_read_is_not_swallowed() -> None:
    provider = FakeEmbeddingProvider(
        document_vectors={},
        query_vector=[1.0, 0.0],
    )
    cached = CachedEmbeddingProvider(
        provider,
        cache=MemoryEmbeddingCache(
            fail_reads_with=asyncio.CancelledError(),
        ),
        ttl_seconds=60,
    )

    with pytest.raises(asyncio.CancelledError):
        await cached.embed_query("明月")

    assert provider.query_calls == []


async def test_empty_documents_skip_cache_and_provider() -> None:
    provider = FakeEmbeddingProvider(
        document_vectors={},
        query_vector=[1.0, 0.0],
    )
    cache = MemoryEmbeddingCache()
    cached = CachedEmbeddingProvider(
        provider,
        cache=cache,
        ttl_seconds=60,
    )

    assert await cached.embed_documents([]) == []
    assert cache.get_calls == []
    assert cache.set_calls == []
    assert provider.document_calls == []


async def test_close_closes_cache_and_provider() -> None:
    provider = FakeEmbeddingProvider(
        document_vectors={},
        query_vector=[1.0, 0.0],
    )
    cache = MemoryEmbeddingCache()
    cached = CachedEmbeddingProvider(
        provider,
        cache=cache,
        ttl_seconds=60,
    )

    await cached.aclose()

    assert cache.closed is True
    assert provider.closed is True


def test_cache_settings_defaults_and_bounds() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite://",
    )

    assert settings.embedding_cache_enabled is False
    assert settings.embedding_cache_ttl_seconds == 3600
    assert settings.embedding_cache_timeout_seconds == 0.5

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="test",
            database_url="sqlite+aiosqlite://",
            embedding_cache_ttl_seconds=0,
        )


def test_redis_embedding_cache_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="URL"):
        RedisEmbeddingCache(" ")
    with pytest.raises(ValueError, match="超时"):
        RedisEmbeddingCache("redis://localhost:6379/0", timeout_seconds=0)


async def test_cache_initialization_failure_keeps_raw_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeEmbeddingProvider(
        document_vectors={"明月": [1.0, 0.0]},
        query_vector=[1.0, 0.0],
    )

    class FakeVectorStore:
        async def aclose(self) -> None:
            return None

    def fail_cache(*_: Any, **__: Any) -> RedisEmbeddingCache:
        raise ValueError("invalid redis configuration")

    monkeypatch.setattr(
        chat_service,
        "create_qwen_embedding_provider",
        lambda settings: provider,
    )
    monkeypatch.setattr(chat_service, "RedisEmbeddingCache", fail_cache)
    monkeypatch.setattr(
        chat_service,
        "create_qdrant_vector_store",
        lambda settings: FakeVectorStore(),
    )
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite://",
        qdrant_url="http://localhost:6335",
        dashscope_api_key=SecretStr("test-key"),
        redis_url="redis://localhost:6379/0",
        embedding_cache_enabled=True,
    )

    resources = await chat_service.create_chat_retrieval_resources(settings)

    assert resources is not None
    assert resources.embedding_provider is provider
    await resources.aclose()
