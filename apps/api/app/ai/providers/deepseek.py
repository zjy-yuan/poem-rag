from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.ai.providers.chat import ChatMessage, ChatModelError, ChatModelPort
from app.core.config import Settings
from app.core.errors import ErrorCode

_MAX_ERROR_MESSAGE_LENGTH = 300


class DeepSeekChatProvider(ChatModelPort):
    """DeepSeek adapter for its OpenAI-compatible chat completions API."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("DeepSeek API Key 未配置")
        if not base_url.strip():
            raise ValueError("DeepSeek Base URL 未配置")
        if not model.strip():
            raise ValueError("DeepSeek 模型 ID 未配置")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    @property
    def model(self) -> str:
        return self._model

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_output_tokens: int,
    ) -> AsyncIterator[str]:
        if not messages:
            raise ChatModelError("聊天消息不能为空")
        if max_output_tokens <= 0:
            raise ChatModelError("最大输出 Token 必须大于 0")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "stream": True,
            "max_tokens": max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        emitted = False
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise ChatModelError(
                        _response_error_message(response, body),
                        status_code=response.status_code,
                    )

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        break
                    delta = _parse_delta(data)
                    if delta:
                        emitted = True
                        yield delta
        except httpx.TimeoutException as exc:
            raise ChatModelError(
                "模型响应超时，请稍后重试",
                code=ErrorCode.MODEL_TIMEOUT,
            ) from exc
        except httpx.TransportError as exc:
            raise ChatModelError("模型服务网络请求失败") from exc

        if not emitted:
            raise ChatModelError(
                "模型返回了空回答",
                code=ErrorCode.CHAT_EMPTY_RESPONSE,
            )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def create_deepseek_chat_provider(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> DeepSeekChatProvider | None:
    if settings.deepseek_api_key is None:
        return None
    api_key = settings.deepseek_api_key.get_secret_value()
    if not api_key.strip():
        return None
    return DeepSeekChatProvider(
        api_key=api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_chat_model,
        timeout_seconds=settings.deepseek_timeout_seconds,
        client=client,
    )


def _parse_delta(data: str) -> str:
    try:
        payload = json.loads(data)
    except ValueError as exc:
        raise ChatModelError("模型返回了无效的流式数据") from exc
    if not isinstance(payload, dict):
        raise ChatModelError("模型返回了无效的流式数据")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    if content is None:
        return ""
    if not isinstance(content, str):
        raise ChatModelError("模型返回了无效的回答内容")
    return content


def _response_error_message(response: httpx.Response, body: bytes) -> str:
    message = f"模型服务返回 HTTP {response.status_code}"
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return message
    if not isinstance(payload, dict):
        return message
    error = payload.get("error")
    if not isinstance(error, dict):
        return message
    upstream_message = error.get("message")
    if not isinstance(upstream_message, str) or not upstream_message.strip():
        return message
    return f"{message}: {upstream_message.strip()[:_MAX_ERROR_MESSAGE_LENGTH]}"
