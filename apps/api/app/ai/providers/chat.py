from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from app.core.errors import ErrorCode

ChatRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: ChatRole
    content: str


class ChatModelError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = ErrorCode.MODEL_PROVIDER_ERROR,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ChatModelPort(Protocol):
    @property
    def model(self) -> str: ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_output_tokens: int,
    ) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...
