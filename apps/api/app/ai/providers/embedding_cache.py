from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from typing import Literal, Protocol

from redis.asyncio import Redis

from app.ai.providers.embedding import EmbeddingProvider

logger = logging.getLogger(__name__)

EmbeddingScope = Literal["documents", "query"]

_CACHE_FORMAT_VERSION = 1
_CACHE_KEY_PREFIX = "poem-rag:embedding:v1"


class EmbeddingCacheStore(Protocol):
    async def get_many(self, keys: list[str]) -> list[str | None]: ...

    async def set_many(
        self,
        values: dict[str, str],
        *,
        ttl_seconds: int,
    ) -> None: ...

    async def aclose(self) -> None: ...


@dataclass(slots=True)
class EmbeddingCacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0
    errors: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 6) if total else 0.0


class RedisEmbeddingCache:
    """Redis storage for exact Embedding vectors."""

    def __init__(
        self,
        redis_url: str,
        *,
        timeout_seconds: float = 0.5,
    ) -> None:
        if not redis_url.strip():
            raise ValueError("Redis URL 不能为空")
        if timeout_seconds <= 0:
            raise ValueError("Redis 缓存超时必须大于 0")
        self._redis = Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=timeout_seconds,
            socket_timeout=timeout_seconds,
        )

    async def get_many(self, keys: list[str]) -> list[str | None]:
        if not keys:
            return []
        values = await self._redis.mget(keys)
        return [
            value if isinstance(value, str) else None
            for value in values
        ]

    async def set_many(
        self,
        values: dict[str, str],
        *,
        ttl_seconds: int,
    ) -> None:
        if not values:
            return
        if ttl_seconds <= 0:
            raise ValueError("Redis 缓存 TTL 必须大于 0")

        pipeline = self._redis.pipeline(transaction=False)
        for key, value in values.items():
            pipeline.set(key, value, ex=ttl_seconds)
        await pipeline.execute()

    async def aclose(self) -> None:
        await self._redis.aclose()


class CachedEmbeddingProvider:
    """Add exact, fail-open caching in front of an Embedding provider."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        cache: EmbeddingCacheStore,
        ttl_seconds: int,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Embedding 缓存 TTL 必须大于 0")
        self._provider = provider
        self._cache = cache
        self._ttl_seconds = ttl_seconds
        self.stats = EmbeddingCacheStats()

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def dimension(self) -> int | None:
        return self._provider.dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        unique_texts = list(dict.fromkeys(texts))
        keys = [
            build_embedding_cache_key(
                text,
                scope="documents",
                model=self.model,
                dimension=self.dimension,
            )
            for text in unique_texts
        ]
        keys_by_text = dict(zip(unique_texts, keys, strict=True))
        cached_vectors = await self._read_vectors(keys)
        vectors_by_key: dict[str, list[float]] = {}
        missing_texts: list[str] = []
        missing_keys: list[str] = []

        for text, key, cached_value in zip(
            unique_texts,
            keys,
            cached_vectors,
            strict=True,
        ):
            vector = _deserialize_vector(
                cached_value,
                expected_dimension=self.dimension,
            )
            if vector is None:
                missing_texts.append(text)
                missing_keys.append(key)
            else:
                vectors_by_key[key] = vector
                self.stats.hits += 1

        if missing_texts:
            self.stats.misses += len(missing_texts)
            generated = await self._provider.embed_documents(missing_texts)
            if len(generated) != len(missing_texts):
                raise RuntimeError(
                    "Embedding Provider 返回的向量数量与缓存 miss 数量不一致"
                )
            _validate_vectors(
                generated,
                texts=missing_texts,
                expected_dimension=self.dimension,
            )
            for key, vector in zip(missing_keys, generated, strict=True):
                vectors_by_key[key] = vector
            await self._write_vectors(
                {
                    key: _serialize_vector(vector)
                    for key, vector in zip(missing_keys, generated, strict=True)
                }
            )

        return [vectors_by_key[keys_by_text[text]] for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        key = build_embedding_cache_key(
            text,
            scope="query",
            model=self.model,
            dimension=self.dimension,
        )
        cached = await self._read_vectors([key])
        vector = _deserialize_vector(
            cached[0],
            expected_dimension=self.dimension,
        )
        if vector is not None:
            self.stats.hits += 1
            return vector

        self.stats.misses += 1
        vector = await self._provider.embed_query(text)
        _validate_vectors(
            [vector],
            texts=[text],
            expected_dimension=self.dimension,
        )
        await self._write_vectors({key: _serialize_vector(vector)})
        return vector

    async def aclose(self) -> None:
        try:
            await self._cache.aclose()
        finally:
            close = getattr(self._provider, "aclose", None)
            if callable(close):
                await close()

    async def _read_vectors(self, keys: list[str]) -> list[str | None]:
        try:
            values = await self._cache.get_many(keys)
        except Exception:
            self.stats.errors += 1
            logger.warning(
                "Embedding cache read failed; falling back to provider",
                exc_info=True,
            )
            return [None] * len(keys)

        if len(values) != len(keys):
            self.stats.errors += 1
            logger.warning(
                "Embedding cache returned an invalid value count; "
                "falling back to provider"
            )
            return [None] * len(keys)
        return values

    async def _write_vectors(self, values: dict[str, str]) -> None:
        if not values:
            return
        try:
            await self._cache.set_many(
                values,
                ttl_seconds=self._ttl_seconds,
            )
        except Exception:
            self.stats.errors += 1
            logger.warning(
                "Embedding cache write failed; provider result is unchanged",
                exc_info=True,
            )
            return
        self.stats.writes += len(values)


def build_embedding_cache_key(
    text: str,
    *,
    scope: EmbeddingScope,
    model: str,
    dimension: int | None,
) -> str:
    canonical = json.dumps(
        [_CACHE_FORMAT_VERSION, scope, model, dimension, text],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_CACHE_KEY_PREFIX}:{scope}:{digest}"


def _serialize_vector(vector: list[float]) -> str:
    return json.dumps(
        {
            "version": _CACHE_FORMAT_VERSION,
            "vector": vector,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _deserialize_vector(
    value: str | None,
    *,
    expected_dimension: int | None,
) -> list[float] | None:
    if value is None:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != _CACHE_FORMAT_VERSION:
        return None
    vector = _coerce_vector(payload.get("vector"))
    if vector is None:
        return None
    if (
        expected_dimension is not None
        and len(vector) != expected_dimension
    ):
        return None
    return vector


def _validate_vectors(
    vectors: list[list[float]],
    *,
    texts: list[str],
    expected_dimension: int | None,
) -> None:
    if len(vectors) != len(texts):
        raise RuntimeError("Embedding Provider 返回的向量数量无效")
    for vector in vectors:
        if not vector or any(
            not math.isfinite(value)
            for value in vector
        ):
            raise RuntimeError("Embedding Provider 返回了无效向量")
        if (
            expected_dimension is not None
            and len(vector) != expected_dimension
        ):
            raise RuntimeError("Embedding Provider 返回的向量维度无效")


def _coerce_vector(value: object) -> list[float] | None:
    if not isinstance(value, list):
        return None
    vector: list[float] = []
    for item in value:
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
        ):
            return None
        vector.append(float(item))
    return vector or None
