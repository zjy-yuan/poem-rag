from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models

from app.ai.providers.vector_store import (
    VectorPoint,
    VectorSearchHit,
    VectorSearchRequest,
)
from app.core.config import Settings

_VECTOR_NAME = "dense"


class VectorStoreError(RuntimeError):
    pass


class QdrantVectorStore:
    """Qdrant adapter that keeps SDK details outside the indexing service."""

    def __init__(
        self,
        *,
        url: str,
        collection: str,
        distance: str = "Cosine",
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        if not url.strip():
            raise VectorStoreError("Qdrant URL 未配置")
        if not collection.strip():
            raise VectorStoreError("Qdrant collection 未配置")
        if timeout_seconds <= 0:
            raise VectorStoreError("Qdrant 超时必须大于 0")

        self._collection = collection
        self._distance = _distance_from_name(distance)
        self._owns_client = client is None
        self._client = client or AsyncQdrantClient(
            url=url,
            api_key=api_key,
            timeout=math.ceil(timeout_seconds),
            check_compatibility=False,
        )

    @property
    def collection(self) -> str:
        return self._collection

    async def ensure_collection(self, *, dimension: int) -> None:
        if dimension <= 0:
            raise VectorStoreError("向量维度必须大于 0")

        try:
            exists = await self._client.collection_exists(self._collection)
            if not exists:
                await self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config={
                        _VECTOR_NAME: qdrant_models.VectorParams(
                            size=dimension,
                            distance=self._distance,
                        )
                    },
                )
                return

            info = await self._client.get_collection(self._collection)
            existing_dimension = _collection_dimension(info.config.params.vectors)
            if existing_dimension is None:
                raise VectorStoreError("Qdrant collection 缺少 dense 向量配置")
            if existing_dimension != dimension:
                raise VectorStoreError(
                    f"Qdrant collection 维度为 {existing_dimension}，"
                    f"当前向量维度为 {dimension}"
                )
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError("Qdrant collection 检查或创建失败") from exc

    async def upsert(self, points: list[VectorPoint]) -> None:
        if not points:
            return
        try:
            await self._client.upsert(
                collection_name=self._collection,
                points=[
                    qdrant_models.PointStruct(
                        id=point.id,
                        vector={_VECTOR_NAME: point.vector},
                        payload=point.payload,
                    )
                    for point in points
                ],
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError("Qdrant 向量写入失败") from exc

    async def delete(self, point_ids: list[str]) -> None:
        if not point_ids:
            return
        try:
            await self._client.delete(
                collection_name=self._collection,
                points_selector=qdrant_models.PointIdsList(points=point_ids),
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreError("Qdrant 向量删除失败") from exc

    async def search(self, request: VectorSearchRequest) -> list[VectorSearchHit]:
        if request.limit <= 0:
            raise VectorStoreError("Qdrant 检索 limit 必须大于 0")
        if not request.vector:
            raise VectorStoreError("Qdrant 查询向量不能为空")

        try:
            response = await self._client.query_points(
                collection_name=self._collection,
                query=request.vector,
                using=_VECTOR_NAME,
                query_filter=_search_filter(request),
                limit=request.limit,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            raise VectorStoreError("Qdrant 向量检索失败") from exc

        return [
            VectorSearchHit(
                id=str(point.id),
                score=float(point.score),
                payload=dict(point.payload or {}),
            )
            for point in response.points
        ]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.close()


def create_qdrant_vector_store(settings: Settings) -> QdrantVectorStore:
    if settings.qdrant_url is None:
        raise VectorStoreError("Qdrant URL 未配置")
    api_key = (
        settings.qdrant_api_key.get_secret_value()
        if settings.qdrant_api_key is not None
        else None
    )
    return QdrantVectorStore(
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        distance=settings.qdrant_distance,
        api_key=api_key or None,
        timeout_seconds=settings.qdrant_timeout_seconds,
    )


def _distance_from_name(name: str) -> qdrant_models.Distance:
    mapping: Mapping[str, qdrant_models.Distance] = {
        "Cosine": qdrant_models.Distance.COSINE,
        "Euclid": qdrant_models.Distance.EUCLID,
        "Dot": qdrant_models.Distance.DOT,
    }
    try:
        return mapping[name]
    except KeyError as exc:
        raise VectorStoreError(f"不支持的 Qdrant 距离函数: {name}") from exc


def _collection_dimension(vectors: Any) -> int | None:
    if isinstance(vectors, dict):
        named = vectors.get(_VECTOR_NAME)
        if isinstance(named, qdrant_models.VectorParams):
            return named.size
    return None


def _search_filter(
    request: VectorSearchRequest,
) -> qdrant_models.Filter | None:
    conditions: list[qdrant_models.FieldCondition] = []
    if request.granularities:
        conditions.append(
            qdrant_models.FieldCondition(
                key="granularity",
                match=qdrant_models.MatchAny(any=list(request.granularities)),
            )
        )
    if request.author_id is not None:
        conditions.append(
            qdrant_models.FieldCondition(
                key="author_id",
                match=qdrant_models.MatchValue(value=request.author_id),
            )
        )
    if request.dynasty_id is not None:
        conditions.append(
            qdrant_models.FieldCondition(
                key="dynasty_id",
                match=qdrant_models.MatchValue(value=request.dynasty_id),
            )
        )
    if request.chunk_strategy is not None:
        conditions.append(
            qdrant_models.FieldCondition(
                key="chunk_strategy",
                match=qdrant_models.MatchValue(value=request.chunk_strategy),
            )
        )
    if not conditions:
        return None
    return qdrant_models.Filter(must=conditions)
