from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimension(self) -> int | None: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...
