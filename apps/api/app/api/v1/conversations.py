from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from starlette.responses import StreamingResponse

from app.ai.providers.chat import ChatModelPort
from app.api.deps import get_chat_provider, get_chat_service, get_current_user
from app.core.response import success_response
from app.models.user import User
from app.schemas.chat import (
    ChatStreamRequest,
    ConversationCreateRequest,
    ConversationUpdateRequest,
)
from app.services.chat import ChatService, ChatStreamEvent

router = APIRouter()


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a conversation",
)
async def create_conversation(
    payload: ConversationCreateRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> Any:
    conversation = await service.create_conversation(user_id=user.id, payload=payload)
    return success_response(conversation, status_code=status.HTTP_201_CREATED)


@router.get("", summary="List current user's conversations")
async def list_conversations(
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> Any:
    conversations = await service.list_conversations(user_id=user.id)
    return success_response(conversations)


@router.get("/{conversation_id}", summary="Get a conversation")
async def get_conversation(
    conversation_id: int,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> Any:
    conversation = await service.get_conversation(
        user_id=user.id,
        conversation_id=conversation_id,
    )
    return success_response(conversation)


@router.patch("/{conversation_id}", summary="Update a conversation")
async def update_conversation(
    conversation_id: int,
    payload: ConversationUpdateRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> Any:
    conversation = await service.update_conversation(
        user_id=user.id,
        conversation_id=conversation_id,
        payload=payload,
    )
    return success_response(conversation)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation",
)
async def delete_conversation(
    conversation_id: int,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> None:
    await service.delete_conversation(
        user_id=user.id,
        conversation_id=conversation_id,
    )


@router.get(
    "/{conversation_id}/messages",
    summary="List messages in a conversation",
)
async def list_messages(
    conversation_id: int,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> Any:
    messages = await service.list_messages(
        user_id=user.id,
        conversation_id=conversation_id,
    )
    return success_response(messages)


@router.post(
    "/{conversation_id}/messages:stream",
    summary="Send a message and stream the RAG answer",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-sent events for retrieval, answer deltas and citations",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream_message(
    conversation_id: int,
    payload: ChatStreamRequest,
    user: Annotated[User, Depends(get_current_user)],
    service: Annotated[ChatService, Depends(get_chat_service)],
    provider: Annotated[ChatModelPort | None, Depends(get_chat_provider)],
) -> StreamingResponse:
    events = await service.start_stream(
        user_id=user.id,
        conversation_id=conversation_id,
        payload=payload,
        provider=provider,
    )
    return StreamingResponse(
        _encode_sse(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _encode_sse(events: AsyncIterator[ChatStreamEvent]) -> AsyncIterator[str]:
    async for event in events:
        data = json.dumps(event.data, ensure_ascii=False, separators=(",", ":"))
        yield f"event: {event.event}\ndata: {data}\n\n"
