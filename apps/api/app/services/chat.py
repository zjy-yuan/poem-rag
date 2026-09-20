from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graphs.rag import CitationDraft, RagChatGraph
from app.ai.providers.chat import ChatMessage, ChatModelError, ChatModelPort, ChatRole
from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.models.conversation import Conversation
from app.models.message import (
    Message,
    MessageCitation,
    MessageRole,
    MessageStatus,
)
from app.repositories.chunks import ChunkRepository
from app.repositories.conversations import ConversationRepository
from app.repositories.messages import MessageRepository
from app.schemas.chat import (
    ChatStreamRequest,
    ConversationCreateRequest,
    ConversationRead,
    ConversationUpdateRequest,
    MessageCitationRead,
    MessageRead,
)
from app.services.evidence_context import PoemContextRetrievalService
from app.services.query_expansion import ExpandedRetrievalService, LexiconQueryRewriter
from app.services.retrieval import RetrievalService

logger = logging.getLogger(__name__)


def build_chat_retrieval(session: AsyncSession) -> PoemContextRetrievalService:
    """Compose the online retrieval stack used by the chat graph."""

    return PoemContextRetrievalService(
        ExpandedRetrievalService(
            RetrievalService(session),
            LexiconQueryRewriter(),
        ),
        ChunkRepository(session),
    )


@dataclass(frozen=True, slots=True)
class ChatStreamEvent:
    event: str
    data: dict[str, Any]


class ChatService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)

    async def create_conversation(
        self,
        *,
        user_id: int,
        payload: ConversationCreateRequest,
    ) -> ConversationRead:
        conversation = await self.conversations.create(
            user_id=user_id,
            title=payload.title,
        )
        await self.session.commit()
        await self.session.refresh(conversation)
        return self._serialize_conversation(conversation)

    async def list_conversations(
        self,
        *,
        user_id: int,
        limit: int = 50,
    ) -> list[ConversationRead]:
        items = await self.conversations.list_for_user(user_id, limit=limit)
        return [self._serialize_conversation(item) for item in items]

    async def get_conversation(
        self,
        *,
        user_id: int,
        conversation_id: int,
    ) -> ConversationRead:
        conversation = await self._require_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        return self._serialize_conversation(conversation)

    async def update_conversation(
        self,
        *,
        user_id: int,
        conversation_id: int,
        payload: ConversationUpdateRequest,
    ) -> ConversationRead:
        conversation = await self._require_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        conversation.title = payload.title
        await self.session.commit()
        await self.session.refresh(conversation)
        return self._serialize_conversation(conversation)

    async def delete_conversation(
        self,
        *,
        user_id: int,
        conversation_id: int,
    ) -> None:
        conversation = await self._require_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        await self.conversations.delete(conversation)
        await self.session.commit()

    async def list_messages(
        self,
        *,
        user_id: int,
        conversation_id: int,
    ) -> list[MessageRead]:
        await self._require_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        messages = await self.messages.list_for_conversation(conversation_id)
        return [self._serialize_message(item) for item in messages]

    async def start_stream(
        self,
        *,
        user_id: int,
        conversation_id: int,
        payload: ChatStreamRequest,
        provider: ChatModelPort | None,
    ) -> AsyncIterator[ChatStreamEvent]:
        conversation = await self._require_conversation(
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if provider is None:
            raise AppError(
                status_code=503,
                code=ErrorCode.CHAT_MODEL_NOT_CONFIGURED,
                message="问答模型尚未配置",
            )
        history = await self._load_history(conversation.id)
        user_message = await self.messages.create(
            conversation_id=conversation.id,
            role=MessageRole.USER.value,
            content=payload.content,
            status=MessageStatus.COMPLETED.value,
        )
        assistant_message = await self.messages.create(
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT.value,
            content="",
            status=MessageStatus.STREAMING.value,
            model=provider.model if provider is not None else None,
        )
        await self.conversations.touch(conversation)
        await self.session.commit()

        return self._stream_events(
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            history=history,
            provider=provider,
        )

    async def _stream_events(
        self,
        *,
        conversation: Conversation,
        user_message: Message,
        assistant_message: Message,
        history: list[ChatMessage],
        provider: ChatModelPort | None,
    ) -> AsyncIterator[ChatStreamEvent]:
        started_at = time.perf_counter()
        content_parts: list[str] = []
        final_answer = ""
        citations: list[CitationDraft] = []
        finish_reason = "stop"
        try:
            yield ChatStreamEvent(
                event="meta",
                data={
                    "message_id": assistant_message.id,
                    "conversation_id": conversation.id,
                },
            )
            graph = RagChatGraph(
                retrieval=build_chat_retrieval(self.session),
                rewriter=LexiconQueryRewriter(),
                provider=provider,
                settings=self.settings,
            )
            async for event in graph.stream(
                query=user_message.content,
                history=history,
            ):
                kind = event.get("kind")
                if kind == "retrieval":
                    yield ChatStreamEvent(
                        event="retrieval",
                        data={
                            "candidate_count": int(event.get("candidate_count", 0)),
                            "selected_count": int(event.get("selected_count", 0)),
                            "strategy": str(event.get("strategy", "")),
                        },
                    )
                elif kind == "delta":
                    text = event.get("text")
                    if isinstance(text, str) and text:
                        content_parts.append(text)
                        yield ChatStreamEvent(event="delta", data={"text": text})
                elif kind == "citation":
                    citation = event.get("citation")
                    if isinstance(citation, CitationDraft):
                        citations.append(citation)
                        yield ChatStreamEvent(
                            event="citation",
                            data=self._serialize_citation_draft(citation),
                        )
                elif kind == "final":
                    answer = event.get("answer")
                    final_answer = answer if isinstance(answer, str) else ""
                    final_citations = event.get("citations")
                    if isinstance(final_citations, list):
                        citations = [
                            item
                            for item in final_citations
                            if isinstance(item, CitationDraft)
                        ]
                    raw_finish_reason = event.get("finish_reason")
                    if isinstance(raw_finish_reason, str) and raw_finish_reason:
                        finish_reason = raw_finish_reason

            if not final_answer.strip():
                raise ChatModelError(
                    "模型返回了空回答",
                    code=ErrorCode.CHAT_EMPTY_RESPONSE,
                )

            assistant_message.content = final_answer
            assistant_message.status = MessageStatus.COMPLETED.value
            assistant_message.latency_ms = _elapsed_ms(started_at)
            await self.messages.add_citations(
                assistant_message,
                [
                    self._create_citation_model(assistant_message.id, citation)
                    for citation in citations
                ],
            )
            await self.conversations.touch(conversation)
            await self.session.commit()
            yield ChatStreamEvent(
                event="done",
                data={
                    "finish_reason": finish_reason,
                    "latency_ms": assistant_message.latency_ms,
                },
            )
        except asyncio.CancelledError:
            await self._mark_cancelled(
                assistant_message=assistant_message,
                content="".join(content_parts),
                started_at=started_at,
            )
            raise
        except GeneratorExit:
            await self._mark_cancelled(
                assistant_message=assistant_message,
                content="".join(content_parts),
                started_at=started_at,
            )
            raise
        except ChatModelError as exc:
            await self._mark_failed(
                assistant_message=assistant_message,
                content="".join(content_parts),
                started_at=started_at,
                error_code=str(exc.code),
            )
            yield ChatStreamEvent(
                event="error",
                data={"code": str(exc.code), "message": str(exc)},
            )
        except AppError as exc:
            await self._mark_failed(
                assistant_message=assistant_message,
                content="".join(content_parts),
                started_at=started_at,
                error_code=str(exc.code),
            )
            yield ChatStreamEvent(
                event="error",
                data={"code": str(exc.code), "message": exc.message},
            )
        except Exception:
            logger.exception(
                "Chat stream failed",
                extra={"conversation_id": conversation.id, "message_id": assistant_message.id},
            )
            await self._mark_failed(
                assistant_message=assistant_message,
                content="".join(content_parts),
                started_at=started_at,
                error_code=ErrorCode.INTERNAL_ERROR.value,
            )
            yield ChatStreamEvent(
                event="error",
                data={
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "message": "问答服务暂时不可用",
                },
            )
        finally:
            if provider is not None:
                try:
                    await provider.aclose()
                except Exception:
                    logger.warning(
                        "Failed to close chat provider",
                        exc_info=True,
                        extra={"conversation_id": conversation.id},
                    )

    async def _load_history(self, conversation_id: int) -> list[ChatMessage]:
        if self.settings.chat_history_limit <= 0:
            return []
        messages = await self.messages.list_for_conversation(conversation_id)
        completed = [
            message
            for message in messages
            if message.status == MessageStatus.COMPLETED.value
            and message.content.strip()
            and message.role in {MessageRole.USER.value, MessageRole.ASSISTANT.value}
        ]
        recent = completed[-self.settings.chat_history_limit :]
        return [
            ChatMessage(role=cast(ChatRole, message.role), content=message.content)
            for message in recent
        ]

    async def _require_conversation(
        self,
        *,
        user_id: int,
        conversation_id: int,
    ) -> Conversation:
        conversation = await self.conversations.get_for_user(conversation_id, user_id)
        if conversation is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.CONVERSATION_NOT_FOUND,
                message="会话不存在",
            )
        return conversation

    async def _mark_failed(
        self,
        *,
        assistant_message: Message,
        content: str,
        started_at: float,
        error_code: str,
    ) -> None:
        assistant_message.content = content
        assistant_message.status = MessageStatus.FAILED.value
        assistant_message.error_code = error_code
        assistant_message.latency_ms = _elapsed_ms(started_at)
        try:
            await self.session.commit()
        except Exception:
            logger.exception("Failed to persist failed chat message")
            await self.session.rollback()

    async def _mark_cancelled(
        self,
        *,
        assistant_message: Message,
        content: str,
        started_at: float,
    ) -> None:
        assistant_message.content = content
        assistant_message.status = MessageStatus.CANCELLED.value
        assistant_message.latency_ms = _elapsed_ms(started_at)
        try:
            await asyncio.shield(self.session.commit())
        except Exception:
            logger.exception("Failed to persist cancelled chat message")
            await self.session.rollback()

    @staticmethod
    def _serialize_conversation(conversation: Conversation) -> ConversationRead:
        return ConversationRead(
            id=conversation.id,
            title=conversation.title,
            status=conversation.status,
            last_message_at=conversation.last_message_at,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
        )

    @staticmethod
    def _serialize_message(message: Message) -> MessageRead:
        return MessageRead(
            id=message.id,
            conversation_id=message.conversation_id,
            role=MessageRole(message.role),
            content=message.content,
            status=MessageStatus(message.status),
            model=message.model,
            latency_ms=message.latency_ms,
            error_code=message.error_code,
            citations=[
                MessageCitationRead(
                    id=citation.id,
                    chunk_id=citation.chunk_id,
                    poem_id=citation.poem_id,
                    poem_version_id=citation.poem_version_id,
                    annotation_id=citation.annotation_id,
                    title=citation.title,
                    author_name=citation.author_name,
                    dynasty_name=citation.dynasty_name,
                    granularity=citation.granularity,
                    text=citation.text,
                    score=citation.score,
                    rank=citation.rank,
                )
                for citation in message.citations
            ],
            created_at=message.created_at,
            updated_at=message.updated_at,
        )

    @staticmethod
    def _serialize_citation_draft(citation: CitationDraft) -> dict[str, Any]:
        return {
            "chunk_id": citation.chunk_id,
            "poem_id": citation.poem_id,
            "poem_version_id": citation.poem_version_id,
            "annotation_id": citation.annotation_id,
            "title": citation.title,
            "author_name": citation.author_name,
            "dynasty_name": citation.dynasty_name,
            "granularity": citation.granularity,
            "text": citation.text,
            "score": citation.score,
            "rank": citation.rank,
        }

    @staticmethod
    def _create_citation_model(
        message_id: int,
        citation: CitationDraft,
    ) -> MessageCitation:
        return MessageCitation(
            message_id=message_id,
            chunk_id=citation.chunk_id,
            poem_id=citation.poem_id,
            poem_version_id=citation.poem_version_id,
            annotation_id=citation.annotation_id,
            title=citation.title,
            author_name=citation.author_name,
            dynasty_name=citation.dynasty_name,
            granularity=citation.granularity,
            text=citation.text,
            score=citation.score,
            rank=citation.rank,
        )


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))
