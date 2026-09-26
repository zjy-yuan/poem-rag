from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graphs.rag import CitationDraft, RagChatGraph
from app.ai.providers.chat import ChatMessage, ChatModelError, ChatModelPort, ChatRole
from app.ai.providers.embedding import EmbeddingProvider
from app.ai.providers.qdrant import VectorStoreError, create_qdrant_vector_store
from app.ai.providers.qwen_embedding import (
    EmbeddingProviderError,
    create_qwen_embedding_provider,
)
from app.ai.providers.vector_store import VectorStorePort
from app.core.config import Settings
from app.core.errors import AppError, ErrorCode
from app.core.request_context import get_request_id
from app.models.chunk import ChunkGranularity
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
from app.services.dense_retrieval import DenseRetrievalService
from app.services.evidence_context import PoemContextRetrievalService
from app.services.hybrid_retrieval import HybridRetrievalService
from app.services.query_expansion import (
    ExpandedRetrievalService,
    LexiconQueryRewriter,
    RepositoryQueryEntityResolver,
)
from app.services.retrieval import (
    EvidenceRetriever,
    RetrievalSearchResult,
    RetrievalService,
)

logger = logging.getLogger(__name__)


class AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


@dataclass(slots=True)
class ChatRetrievalResources:
    """Long-lived external clients shared by online chat requests."""

    embedding_provider: EmbeddingProvider | None = None
    vector_store: VectorStorePort | None = None

    @property
    def configured(self) -> bool:
        return (
            self.embedding_provider is not None
            and self.vector_store is not None
        )

    async def aclose(self) -> None:
        await _close_resources(
            [
                cast(AsyncCloseable, resource)
                for resource in (
                    self.vector_store,
                    self.embedding_provider,
                )
                if resource is not None
            ]
        )


@dataclass(slots=True)
class ChatRetrievalStack:
    service: PoemContextRetrievalService
    resources: tuple[AsyncCloseable, ...] = ()

    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
        granularities: list[ChunkGranularity] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
    ) -> RetrievalSearchResult:
        return await self.service.search_evidence(
            query=query,
            limit=limit,
            granularities=granularities,
            author_id=author_id,
            dynasty_id=dynasty_id,
        )

    async def aclose(self) -> None:
        await _close_resources(list(self.resources))


async def build_chat_retrieval(
    session: AsyncSession,
    settings: Settings,
    *,
    resources: ChatRetrievalResources | None = None,
) -> ChatRetrievalStack:
    """Compose the online retrieval stack used by the chat graph."""

    lexical: EvidenceRetriever = RetrievalService(session)
    owned_resources: list[AsyncCloseable] = []
    retrieval: EvidenceRetriever = lexical

    embedding_provider = (
        resources.embedding_provider
        if resources is not None and resources.configured
        else None
    )
    vector_store = (
        resources.vector_store
        if resources is not None and resources.configured
        else None
    )
    if embedding_provider is None or vector_store is None:
        created_resources = await create_chat_retrieval_resources(settings)
        if created_resources is not None:
            embedding_provider = created_resources.embedding_provider
            vector_store = created_resources.vector_store
            owned_resources.extend(
                cast(AsyncCloseable, resource)
                for resource in (
                    embedding_provider,
                    vector_store,
                )
                if resource is not None
            )

    if embedding_provider is not None and vector_store is not None:
        retrieval = HybridRetrievalService(
            lexical,
            DenseRetrievalService(
                session,
                embedding_provider=embedding_provider,
                vector_store=vector_store,
                min_score=settings.chat_dense_min_score,
            ),
            fallback_on_dense_error=True,
        )
    retrieval = ExpandedRetrievalService(
        retrieval,
        LexiconQueryRewriter(),
        strategy_name="expanded-hybrid-rrf-v1",
        entity_resolver=RepositoryQueryEntityResolver(session),
        max_variants=settings.chat_query_variant_limit,
    )

    return ChatRetrievalStack(
        service=PoemContextRetrievalService(
            retrieval,
            ChunkRepository(session),
        ),
        resources=tuple(owned_resources),
    )


async def create_chat_retrieval_resources(
    settings: Settings,
) -> ChatRetrievalResources | None:
    """Create shared retrieval clients, or return None when unconfigured."""

    if not settings.qdrant_url or settings.dashscope_api_key is None:
        return None

    created: list[AsyncCloseable] = []
    try:
        embedding_provider = create_qwen_embedding_provider(settings)
        created.append(embedding_provider)
        vector_store = create_qdrant_vector_store(settings)
        created.append(vector_store)
    except (EmbeddingProviderError, VectorStoreError) as exc:
        logger.warning(
            "Dense retrieval configuration is unavailable; "
            "falling back to expanded lexical retrieval (%s)",
            type(exc).__name__,
        )
        await _close_resources(created)
        return None

    return ChatRetrievalResources(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )


async def _close_resources(resources: list[AsyncCloseable]) -> None:
    for resource in reversed(resources):
        try:
            await resource.aclose()
        except Exception:
            logger.warning(
                "Failed to close chat retrieval resource",
                exc_info=True,
            )


@dataclass(frozen=True, slots=True)
class ChatStreamEvent:
    event: str
    data: dict[str, Any]


class ChatService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        retrieval_resources: ChatRetrievalResources | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.retrieval_resources = retrieval_resources
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
        request_id = get_request_id()
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
            request_id=request_id,
        )

    async def _stream_events(
        self,
        *,
        conversation: Conversation,
        user_message: Message,
        assistant_message: Message,
        history: list[ChatMessage],
        provider: ChatModelPort | None,
        request_id: str,
    ) -> AsyncIterator[ChatStreamEvent]:
        started_at = time.perf_counter()
        content_parts: list[str] = []
        final_answer = ""
        citations: list[CitationDraft] = []
        finish_reason = "stop"
        status = "failed"
        error_code: str | None = None
        timings: dict[str, float] = {}
        ttft_ms: int | None = None
        candidate_count = 0
        selected_count = 0
        strategy = ""
        assessment_status: str | None = None
        assessment_reason_code: str | None = None
        try:
            yield ChatStreamEvent(
                event="meta",
                data={
                    "message_id": assistant_message.id,
                    "conversation_id": conversation.id,
                },
            )
            retrieval = await build_chat_retrieval(
                self.session,
                self.settings,
                resources=self.retrieval_resources,
            )
            graph = RagChatGraph(
                retrieval=retrieval,
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
                    candidate_count = int(event.get("candidate_count", 0))
                    selected_count = int(event.get("selected_count", 0))
                    strategy = str(event.get("strategy", ""))
                    yield ChatStreamEvent(
                        event="retrieval",
                        data={
                            "candidate_count": candidate_count,
                            "selected_count": selected_count,
                            "strategy": strategy,
                        },
                    )
                elif kind == "timing":
                    stage = event.get("stage")
                    duration_ms = event.get("duration_ms")
                    if (
                        isinstance(stage, str)
                        and stage
                        and isinstance(duration_ms, (int, float))
                        and not isinstance(duration_ms, bool)
                    ):
                        timings[stage] = max(0.0, float(duration_ms))
                elif kind == "assessment":
                    raw_status = event.get("status")
                    if isinstance(raw_status, str) and raw_status:
                        assessment_status = raw_status
                    raw_reason_code = event.get("reason_code")
                    if isinstance(raw_reason_code, str) and raw_reason_code:
                        assessment_reason_code = raw_reason_code
                elif kind == "delta":
                    text = event.get("text")
                    if isinstance(text, str) and text:
                        if ttft_ms is None:
                            ttft_ms = _elapsed_ms(started_at)
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
            status = "completed"
            yield ChatStreamEvent(
                event="done",
                data={
                    "finish_reason": finish_reason,
                    "latency_ms": assistant_message.latency_ms,
                },
            )
        except asyncio.CancelledError:
            status = "cancelled"
            await self._mark_cancelled(
                assistant_message=assistant_message,
                content="".join(content_parts),
                started_at=started_at,
            )
            raise
        except GeneratorExit:
            status = "cancelled"
            await self._mark_cancelled(
                assistant_message=assistant_message,
                content="".join(content_parts),
                started_at=started_at,
            )
            raise
        except ChatModelError as exc:
            status = "failed"
            error_code = str(exc.code)
            await self._mark_failed(
                assistant_message=assistant_message,
                content="".join(content_parts),
                started_at=started_at,
                error_code=error_code,
            )
            yield ChatStreamEvent(
                event="error",
                data={"code": error_code, "message": str(exc)},
            )
        except AppError as exc:
            status = "failed"
            error_code = str(exc.code)
            await self._mark_failed(
                assistant_message=assistant_message,
                content="".join(content_parts),
                started_at=started_at,
                error_code=error_code,
            )
            yield ChatStreamEvent(
                event="error",
                data={"code": error_code, "message": exc.message},
            )
        except Exception:
            status = "failed"
            error_code = ErrorCode.INTERNAL_ERROR.value
            logger.exception(
                "Chat stream failed",
                extra={"conversation_id": conversation.id, "message_id": assistant_message.id},
            )
            await self._mark_failed(
                assistant_message=assistant_message,
                content="".join(content_parts),
                started_at=started_at,
                error_code=error_code,
            )
            yield ChatStreamEvent(
                event="error",
                data={
                    "code": error_code,
                    "message": "问答服务暂时不可用",
                },
            )
        finally:
            total_ms = _elapsed_ms(started_at)
            if "retrieval" in locals():
                await retrieval.aclose()
            if provider is not None:
                try:
                    await provider.aclose()
                except Exception:
                    logger.warning(
                        "Failed to close chat provider",
                        exc_info=True,
                        extra={"conversation_id": conversation.id},
                    )
            self._log_stream_metrics(
                request_id=request_id,
                conversation=conversation,
                assistant_message=assistant_message,
                status=status,
                error_code=error_code,
                finish_reason=finish_reason,
                candidate_count=candidate_count,
                selected_count=selected_count,
                strategy=strategy,
                assessment_status=assessment_status,
                assessment_reason_code=assessment_reason_code,
                timings=timings,
                ttft_ms=ttft_ms,
                total_ms=total_ms,
            )

    def _log_stream_metrics(
        self,
        *,
        request_id: str,
        conversation: Conversation,
        assistant_message: Message,
        status: str,
        error_code: str | None,
        finish_reason: str,
        candidate_count: int,
        selected_count: int,
        strategy: str,
        assessment_status: str | None,
        assessment_reason_code: str | None,
        timings: dict[str, float],
        ttft_ms: int | None,
        total_ms: int,
    ) -> None:
        log = logger.warning if status == "failed" else logger.info
        log(
            "Chat stream completed",
            extra={
                "request_id": request_id,
                "conversation_id": conversation.id,
                "assistant_message_id": assistant_message.id,
                "status": status,
                "error_code": error_code,
                "finish_reason": finish_reason,
                "candidate_count": candidate_count,
                "selected_count": selected_count,
                "strategy": strategy,
                "assessment_status": assessment_status,
                "assessment_reason_code": assessment_reason_code,
                "query_variant_limit": self.settings.chat_query_variant_limit,
                "rewrite_ms": timings.get("rewrite"),
                "retrieval_ms": timings.get("retrieval"),
                "assess_ms": timings.get("assess"),
                "generation_ms": timings.get("generation"),
                "validate_ms": timings.get("validate"),
                "ttft_ms": ttft_ms,
                "total_ms": total_ms,
            },
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
