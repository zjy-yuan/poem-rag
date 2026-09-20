from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.ai.providers.embedding import EmbeddingProvider
from app.core.config import Settings

_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_MAX_ERROR_MESSAGE_LENGTH = 300


class EmbeddingProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class QwenEmbeddingConfig:
    api_key: str = field(repr=False)
    base_url: str
    model: str = "text-embedding-v4"
    dimension: int | None = None
    batch_size: int = 10
    timeout_seconds: float = 30.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise EmbeddingProviderError("Qwen Embedding API Key 未配置")
        if not self.base_url.strip():
            raise EmbeddingProviderError("Qwen Embedding Base URL 未配置")
        if not self.model.strip():
            raise EmbeddingProviderError("Qwen Embedding 模型 ID 未配置")
        if self.dimension is not None and self.dimension <= 0:
            raise EmbeddingProviderError("Qwen Embedding 维度必须大于 0")
        if self.batch_size <= 0:
            raise EmbeddingProviderError("Qwen Embedding 批量大小必须大于 0")
        if self.timeout_seconds <= 0:
            raise EmbeddingProviderError("Qwen Embedding 超时必须大于 0")
        if self.max_retries < 0:
            raise EmbeddingProviderError("Qwen Embedding 最大重试次数不能小于 0")
        if self.retry_backoff_seconds < 0:
            raise EmbeddingProviderError("Qwen Embedding 重试退避时间不能小于 0")


class QwenEmbeddingProvider(EmbeddingProvider):
    """Qwen Embedding adapter for the DashScope OpenAI-compatible API."""

    def __init__(
        self,
        config: QwenEmbeddingConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=config.timeout_seconds)

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def dimension(self) -> int | None:
        return self._config.dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        _validate_texts(texts)

        embeddings: list[list[float]] = []
        batch_size = self._config.batch_size
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            embeddings.extend(await self._embed_batch(batch))
        return embeddings

    async def embed_query(self, text: str) -> list[float]:
        _validate_texts([text])
        embeddings = await self._embed_batch([text])
        return embeddings[0]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "input": texts,
        }
        if self._config.dimension is not None:
            payload["dimensions"] = self._config.dimension

        response = await self._post_with_retry(payload)
        return self._parse_embeddings(response, expected_count=len(texts))

    async def _post_with_retry(self, payload: dict[str, Any]) -> httpx.Response:
        url = f"{self._config.base_url.rstrip('/')}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(self._config.max_retries + 1):
            try:
                response = await self._client.post(url, headers=headers, json=payload)
            except httpx.TransportError as exc:
                if attempt < self._config.max_retries:
                    await self._sleep_before_retry(attempt)
                    continue
                raise EmbeddingProviderError(
                    "Qwen Embedding 网络请求失败",
                    retryable=True,
                ) from exc

            if response.status_code < 400:
                return response
            if (
                response.status_code in _RETRYABLE_STATUS_CODES
                and attempt < self._config.max_retries
            ):
                await self._sleep_before_retry(attempt)
                continue

            raise EmbeddingProviderError(
                _response_error_message(response),
                retryable=response.status_code in _RETRYABLE_STATUS_CODES,
                status_code=response.status_code,
            )

        raise EmbeddingProviderError(
            "Qwen Embedding 重试次数已耗尽",
            retryable=True,
        )

    async def _sleep_before_retry(self, attempt: int) -> None:
        delay = self._config.retry_backoff_seconds * (2**attempt)
        if delay > 0:
            await asyncio.sleep(delay)

    def _parse_embeddings(
        self,
        response: httpx.Response,
        *,
        expected_count: int,
    ) -> list[list[float]]:
        try:
            body = response.json()
        except ValueError as exc:
            raise EmbeddingProviderError(
                "Qwen Embedding 返回了无效 JSON",
                retryable=True,
                status_code=response.status_code,
            ) from exc

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list) or len(data) != expected_count:
            raise EmbeddingProviderError(
                "Qwen Embedding 返回的向量数量与输入不一致",
                retryable=True,
                status_code=response.status_code,
            )

        by_index: dict[int, list[float]] = {}
        inferred_dimension: int | None = self._config.dimension
        for position, item in enumerate(data):
            if not isinstance(item, dict):
                raise EmbeddingProviderError("Qwen Embedding 返回项格式无效")
            index = item.get("index", position)
            embedding = item.get("embedding")
            if (
                not isinstance(index, int)
                or index < 0
                or index >= expected_count
                or index in by_index
            ):
                raise EmbeddingProviderError("Qwen Embedding 返回的索引无效")
            vector = _coerce_vector(embedding)
            if not vector:
                raise EmbeddingProviderError("Qwen Embedding 返回了空向量")
            if inferred_dimension is None:
                inferred_dimension = len(vector)
            if len(vector) != inferred_dimension:
                raise EmbeddingProviderError(
                    "Qwen Embedding 返回的向量维度不一致",
                    status_code=response.status_code,
                )
            by_index[index] = vector

        return [by_index[index] for index in range(expected_count)]


def create_qwen_embedding_provider(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> QwenEmbeddingProvider:
    api_key = (
        settings.dashscope_api_key.get_secret_value()
        if settings.dashscope_api_key is not None
        else ""
    )
    return QwenEmbeddingProvider(
        QwenEmbeddingConfig(
            api_key=api_key,
            base_url=settings.dashscope_base_url,
            model=settings.qwen_embedding_model,
            dimension=settings.qwen_embedding_dimension,
            batch_size=settings.qwen_embedding_batch_size,
            timeout_seconds=settings.qwen_embedding_timeout_seconds,
            max_retries=settings.qwen_embedding_max_retries,
            retry_backoff_seconds=settings.qwen_embedding_retry_backoff_seconds,
        ),
        client=client,
    )


def _validate_texts(texts: list[str]) -> None:
    if any(not text.strip() for text in texts):
        raise ValueError("Embedding 输入文本不能为空")


def _coerce_vector(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    vector: list[float] = []
    for item in value:
        if not isinstance(item, (int, float)) or isinstance(item, bool):
            return []
        vector.append(float(item))
    return vector


def _response_error_message(response: httpx.Response) -> str:
    message = f"Qwen Embedding 服务返回 HTTP {response.status_code}"
    try:
        body = response.json()
    except ValueError:
        return message
    if not isinstance(body, dict):
        return message
    error = body.get("error")
    if not isinstance(error, dict):
        return message
    upstream_message = error.get("message")
    if not isinstance(upstream_message, str) or not upstream_message.strip():
        return message
    return f"{message}: {upstream_message.strip()[:_MAX_ERROR_MESSAGE_LENGTH]}"
