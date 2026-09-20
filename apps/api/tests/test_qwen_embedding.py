from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.ai.providers.qwen_embedding import (
    EmbeddingProviderError,
    QwenEmbeddingConfig,
    QwenEmbeddingProvider,
    create_qwen_embedding_provider,
)
from app.core.config import Settings
from pydantic import SecretStr


def _config(**overrides: Any) -> QwenEmbeddingConfig:
    values: dict[str, Any] = {
        "api_key": "test-api-key",
        "base_url": "https://embedding.example.test/v1",
        "model": "text-embedding-test",
        "dimension": 3,
        "batch_size": 2,
        "retry_backoff_seconds": 0,
    }
    values.update(overrides)
    return QwenEmbeddingConfig(**values)


def _embedding_response(request: httpx.Request, *, reverse: bool = False) -> httpx.Response:
    payload = json.loads(request.content)
    inputs = payload["input"]
    data = [
        {
            "index": index,
            "embedding": [float(index), float(index + 1), float(index + 2)],
        }
        for index, _ in enumerate(inputs)
    ]
    if reverse:
        data.reverse()
    return httpx.Response(200, json={"data": data, "model": payload["model"]})


async def test_qwen_embedding_batches_and_uses_configured_dimension() -> None:
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _embedding_response(request, reverse=True)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = QwenEmbeddingProvider(_config(), client=client)
        vectors = await provider.embed_documents(["诗一", "诗二", "诗三"])

    assert [len(batch["input"]) for batch in requests] == [2, 1]
    assert all(batch["model"] == "text-embedding-test" for batch in requests)
    assert all(batch["dimensions"] == 3 for batch in requests)
    assert vectors == [
        [0.0, 1.0, 2.0],
        [1.0, 2.0, 3.0],
        [0.0, 1.0, 2.0],
    ]


async def test_qwen_embedding_retries_retryable_status() -> None:
    call_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return _embedding_response(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = QwenEmbeddingProvider(_config(), client=client)
        vectors = await provider.embed_query("明月")

    assert call_count == 2
    assert vectors == [0.0, 1.0, 2.0]


async def test_qwen_embedding_does_not_retry_auth_error() -> None:
    call_count = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = QwenEmbeddingProvider(_config(), client=client)
        with pytest.raises(EmbeddingProviderError) as exc_info:
            await provider.embed_query("明月")

    assert call_count == 1
    assert exc_info.value.status_code == 401
    assert exc_info.value.retryable is False
    assert "invalid key" in str(exc_info.value)


async def test_qwen_embedding_rejects_count_and_dimension_mismatch() -> None:
    async def count_mismatch(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(count_mismatch)) as client:
        provider = QwenEmbeddingProvider(_config(), client=client)
        with pytest.raises(EmbeddingProviderError, match="数量"):
            await provider.embed_documents(["诗一", "诗二"])

    async def dimension_mismatch(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 2.0]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(dimension_mismatch)) as client:
        provider = QwenEmbeddingProvider(_config(), client=client)
        with pytest.raises(EmbeddingProviderError, match="维度"):
            await provider.embed_query("明月")


async def test_qwen_embedding_empty_input_skips_http() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("empty input should not issue an HTTP request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = QwenEmbeddingProvider(_config(), client=client)
        assert await provider.embed_documents([]) == []


def test_create_qwen_embedding_provider_requires_api_key() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite://",
        dashscope_api_key=None,
    )

    with pytest.raises(EmbeddingProviderError, match="API Key"):
        create_qwen_embedding_provider(settings)


def test_create_qwen_embedding_provider_uses_settings() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite://",
        dashscope_api_key=SecretStr("configured-key"),
        dashscope_base_url="https://configured.example.test/v1",
        qwen_embedding_model="configured-model",
        qwen_embedding_dimension=1024,
        qwen_embedding_batch_size=5,
        qwen_embedding_timeout_seconds=12.5,
        qwen_embedding_max_retries=1,
        qwen_embedding_retry_backoff_seconds=0.25,
    )

    provider = create_qwen_embedding_provider(settings)

    assert provider.model == "configured-model"
    assert provider.dimension == 1024


def test_settings_treat_empty_embedding_dimension_as_none() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite://",
        qwen_embedding_dimension="",
    )

    assert settings.qwen_embedding_dimension is None
