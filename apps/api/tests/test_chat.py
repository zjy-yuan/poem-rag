from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

from app.ai.graphs.rag import NO_EVIDENCE_ANSWER
from app.ai.providers.chat import ChatMessage, ChatModelError
from app.api.deps import get_chat_provider
from app.core.errors import ErrorCode
from fastapi.testclient import TestClient
from test_rag_corpus import _load_versions
from test_retrieval import _create_catalog, _create_poem, _publish, _rebuild_chunks


class FakeChatProvider:
    def __init__(
        self,
        deltas: Sequence[str] = (),
        *,
        error: Exception | None = None,
    ) -> None:
        self._deltas = list(deltas)
        self._error = error
        self.calls: list[list[ChatMessage]] = []
        self.closed = False

    @property
    def model(self) -> str:
        return "fake-chat-v1"

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_output_tokens: int,
    ) -> AsyncIterator[str]:
        self.calls.append(list(messages))
        if self._error is not None:
            raise self._error
        for delta in self._deltas:
            yield delta

    async def aclose(self) -> None:
        self.closed = True


def _register(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "问答读者",
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _create_conversation(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"title": "月意象问答"},
    )
    assert response.status_code == 201
    return response.json()["data"]


def _use_provider(client: TestClient, provider: FakeChatProvider | None) -> None:
    client.app.dependency_overrides[get_chat_provider] = lambda: provider


def _seed_evidence(client: TestClient) -> dict[str, str]:
    headers, dynasty, author = _create_catalog(client)
    poem = _create_poem(
        client,
        headers,
        title="静夜思",
        content="床前明月光，疑是地上霜。\n举头望明月，低头思故乡。",
        author_id=author["id"],
        dynasty_id=dynasty["id"],
    )
    _publish(client, headers, poem["id"])
    portal = client.portal
    assert portal is not None
    versions = portal.call(
        _load_versions,
        client.app.state.session_factory,
        poem["id"],
    )
    _rebuild_chunks(client, versions[0].id)
    return headers


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        event_line = next(line for line in lines if line.startswith("event: "))
        data_line = next(line for line in lines if line.startswith("data: "))
        events.append(
            (
                event_line.removeprefix("event: "),
                json.loads(data_line.removeprefix("data: ")),
            )
        )
    return events


def test_conversation_endpoints_require_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/conversations").status_code == 401
    assert client.post("/api/v1/conversations", json={}).status_code == 401


def test_conversation_crud_and_ownership(client: TestClient) -> None:
    owner = _register(client, "owner@example.com")
    other = _register(client, "other@example.com")
    conversation = _create_conversation(client, owner)

    response = client.patch(
        f"/api/v1/conversations/{conversation['id']}",
        headers=owner,
        json={"title": "更新后的标题"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["title"] == "更新后的标题"

    hidden = client.get(
        f"/api/v1/conversations/{conversation['id']}",
        headers=other,
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"

    listed = client.get("/api/v1/conversations", headers=owner).json()["data"]
    assert [item["id"] for item in listed] == [conversation["id"]]

    deleted = client.delete(
        f"/api/v1/conversations/{conversation['id']}",
        headers=owner,
    )
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/api/v1/conversations", headers=owner).json()["data"] == []


def test_stream_persists_messages_citations_and_events(client: TestClient) -> None:
    _seed_evidence(client)
    user = _register(client, "stream@example.com")
    conversation = _create_conversation(client, user)
    provider = FakeChatProvider(["月光常与", "思乡相连。"])
    _use_provider(client, provider)

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages:stream",
        headers={**user, "Accept": "text/event-stream"},
        json={"content": "赏析静夜思里的月亮有什么含义？"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    event_names = [name for name, _ in events]
    assert event_names[0] == "meta"
    assert event_names[1] == "retrieval"
    assert "delta" in event_names
    assert "citation" in event_names
    assert event_names[-1] == "done"
    assert event_names.index("done") > event_names.index("citation")
    assert provider.closed is True
    assert provider.calls

    messages = client.get(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=user,
    ).json()["data"]
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "赏析静夜思里的月亮有什么含义？"
    assert messages[1]["content"] == "月光常与思乡相连。"
    assert messages[1]["status"] == "completed"
    assert messages[1]["model"] == "fake-chat-v1"
    assert messages[1]["citations"]
    citation = messages[1]["citations"][0]
    assert citation["title"] == "静夜思"
    assert citation["chunk_id"] is not None
    assert citation["text"]


def test_no_evidence_returns_stable_refusal_without_model_call(
    client: TestClient,
) -> None:
    user = _register(client, "refusal@example.com")
    conversation = _create_conversation(client, user)
    provider = FakeChatProvider(["不应被调用"])
    _use_provider(client, provider)

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages:stream",
        headers=user,
        json={"content": "完全不存在的诗句"},
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert [name for name, _ in events] == [
        "meta",
        "retrieval",
        "delta",
        "done",
    ]
    delta = next(data for name, data in events if name == "delta")
    assert delta["text"] == NO_EVIDENCE_ANSWER
    assert provider.calls == []
    assert provider.closed is True

    messages = client.get(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=user,
    ).json()["data"]
    assert messages[1]["status"] == "completed"
    assert messages[1]["content"] == NO_EVIDENCE_ANSWER
    assert messages[1]["citations"] == []


def test_model_failure_emits_error_after_retrieval_and_stops_deltas(
    client: TestClient,
) -> None:
    _seed_evidence(client)
    user = _register(client, "failure@example.com")
    conversation = _create_conversation(client, user)
    provider = FakeChatProvider(
        error=ChatModelError("模型响应超时，请稍后重试", code=ErrorCode.MODEL_TIMEOUT),
    )
    _use_provider(client, provider)

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages:stream",
        headers=user,
        json={"content": "赏析静夜思里的月亮有什么含义？"},
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    event_names = [name for name, _ in events]
    assert event_names[:2] == ["meta", "retrieval"]
    assert "delta" not in event_names
    assert "citation" not in event_names
    assert event_names[-1] == "error"
    assert events[-1][1]["code"] == ErrorCode.MODEL_TIMEOUT.value

    messages = client.get(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=user,
    ).json()["data"]
    assert messages[1]["status"] == "failed"
    assert messages[1]["error_code"] == ErrorCode.MODEL_TIMEOUT.value


def test_unconfigured_model_returns_503_without_persisting_messages(
    client: TestClient,
) -> None:
    user = _register(client, "unconfigured@example.com")
    conversation = _create_conversation(client, user)
    _use_provider(client, None)

    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages:stream",
        headers=user,
        json={"content": "解释静夜思"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == ErrorCode.CHAT_MODEL_NOT_CONFIGURED.value
    messages = client.get(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers=user,
    ).json()["data"]
    assert messages == []
