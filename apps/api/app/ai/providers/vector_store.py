from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class VectorSearchRequest:
    vector: list[float]
    limit: int
    granularities: tuple[str, ...] = ()
    author_id: int | None = None
    dynasty_id: int | None = None
    chunk_strategy: str | None = None


@dataclass(frozen=True, slots=True)
class VectorSearchHit:
    id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VectorPoint:
    id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)


class VectorStorePort(Protocol):
    @property
    def collection(self) -> str: ...

    async def ensure_collection(self, *, dimension: int) -> None: ...

    async def upsert(self, points: list[VectorPoint]) -> None: ...

    async def delete(self, point_ids: list[str]) -> None: ...

    async def search(self, request: VectorSearchRequest) -> list[VectorSearchHit]: ...
