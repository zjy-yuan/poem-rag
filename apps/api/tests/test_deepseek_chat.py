from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.ai.providers.chat import ChatMessage, ChatModelError
from app.ai.providers.deepseek import DeepSeekChatProvider
from app.core.errors import ErrorCode


async def test_deepseek_generate_uses_json_response_format() -> None:
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answerable": true, "reason_code": "supported", '
                                '"missing": []}'
                            )
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekChatProvider(
            api_key="test-api-key",
            base_url="https://chat.example.test/v1",
            model="deepseek-chat",
            client=client,
        )
        content = await provider.generate(
            [ChatMessage(role="user", content="判断证据是否足够")],
            max_output_tokens=64,
            temperature=0.0,
            response_format="json_object",
        )

    assert content.startswith('{"answerable": true')
    assert len(requests) == 1
    payload = requests[0]
    assert payload["model"] == "deepseek-chat"
    assert payload["stream"] is False
    assert payload["max_tokens"] == 64
    assert payload["temperature"] == 0.0
    assert payload["response_format"] == {"type": "json_object"}


async def test_deepseek_generate_rejects_empty_content() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": " "}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekChatProvider(
            api_key="test-api-key",
            base_url="https://chat.example.test/v1",
            model="deepseek-chat",
            client=client,
        )
        with pytest.raises(ChatModelError) as exc_info:
            await provider.generate(
                [ChatMessage(role="user", content="判断证据是否足够")],
                max_output_tokens=64,
            )

    assert exc_info.value.code == ErrorCode.CHAT_EMPTY_RESPONSE


async def test_deepseek_generate_maps_http_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": {"message": "invalid key"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DeepSeekChatProvider(
            api_key="test-api-key",
            base_url="https://chat.example.test/v1",
            model="deepseek-chat",
            client=client,
        )
        with pytest.raises(ChatModelError) as exc_info:
            await provider.generate(
                [ChatMessage(role="user", content="判断证据是否足够")],
                max_output_tokens=64,
            )

    assert exc_info.value.status_code == 401
    assert "invalid key" in str(exc_info.value)
