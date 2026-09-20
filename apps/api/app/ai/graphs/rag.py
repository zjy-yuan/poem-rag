from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.ai.providers.chat import ChatMessage, ChatModelError, ChatModelPort
from app.core.config import Settings
from app.core.errors import ErrorCode
from app.models.chunk import ChunkGranularity
from app.schemas.retrieval import RetrievalEvidence
from app.services.query_expansion import ExpandedRetrievalService, QueryRewriter

NO_EVIDENCE_ANSWER = "没有在当前诗词库中找到足够依据，暂时无法回答这个问题。"
_MAX_CONTEXT_CHUNK_LENGTH = 1200
_MAX_CONTEXT_LENGTH = 6000


@dataclass(frozen=True, slots=True)
class CitationDraft:
    chunk_id: int
    poem_id: int
    poem_version_id: int
    annotation_id: int | None
    title: str
    author_name: str | None
    dynasty_name: str | None
    granularity: str
    text: str
    score: float
    rank: int

    @classmethod
    def from_evidence(
        cls,
        evidence: RetrievalEvidence,
        *,
        rank: int,
    ) -> CitationDraft:
        return cls(
            chunk_id=evidence.chunk_id,
            poem_id=evidence.poem_id,
            poem_version_id=evidence.poem_version_id,
            annotation_id=evidence.annotation_id,
            title=evidence.title,
            author_name=evidence.author_name,
            dynasty_name=evidence.dynasty_name,
            granularity=evidence.granularity.value,
            text=evidence.text,
            score=evidence.score,
            rank=rank,
        )


class RagChatState(TypedDict, total=False):
    query: str
    history: list[ChatMessage]
    rewritten_query: str
    matched_concepts: list[str]
    matched_entities: list[str]
    evidence: list[RetrievalEvidence]
    candidate_count: int
    answer: str
    citations: list[CitationDraft]
    finish_reason: str


class RagChatGraph:
    """Four-node poetry RAG flow with custom streaming events."""

    def __init__(
        self,
        *,
        retrieval: ExpandedRetrievalService,
        rewriter: QueryRewriter,
        provider: ChatModelPort | None,
        settings: Settings,
    ) -> None:
        self.retrieval = retrieval
        self.rewriter = rewriter
        self.provider = provider
        self.settings = settings
        self._graph = self._build()

    def _build(self) -> Any:
        graph = StateGraph(RagChatState)
        graph.add_node("rewrite", self._rewrite)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("generate", self._generate)
        graph.add_node("validate", self._validate)
        graph.add_edge(START, "rewrite")
        graph.add_edge("rewrite", "retrieve")
        graph.add_edge("retrieve", "generate")
        graph.add_edge("generate", "validate")
        graph.add_edge("validate", END)
        return graph.compile()

    async def stream(
        self,
        *,
        query: str,
        history: Sequence[ChatMessage],
    ) -> AsyncIterator[dict[str, Any]]:
        initial_state: RagChatState = {
            "query": query,
            "history": list(history),
        }
        async for event in self._graph.astream(initial_state, stream_mode="custom"):
            if isinstance(event, dict):
                yield cast(dict[str, Any], event)

    async def _rewrite(self, state: RagChatState) -> RagChatState:
        rewrite = await self.rewriter.rewrite(state["query"])
        variants = [state["query"], *rewrite.variants]
        return {
            "rewritten_query": " ".join(dict.fromkeys(variants)),
            "matched_concepts": list(rewrite.matched_concepts),
            "matched_entities": list(rewrite.matched_entities),
        }

    async def _retrieve(self, state: RagChatState) -> RagChatState:
        result = await self.retrieval.search_evidence(
            query=state.get("rewritten_query") or state["query"],
            limit=self.settings.chat_retrieval_limit,
            granularities=[
                ChunkGranularity.POEM,
                ChunkGranularity.LINE,
                ChunkGranularity.NOTE,
            ],
        )
        get_stream_writer()(
            {
                "kind": "retrieval",
                "candidate_count": result.candidate_count,
                "selected_count": len(result.items),
                "strategy": result.strategy,
            }
        )
        return {
            "evidence": result.items,
            "candidate_count": result.candidate_count,
        }

    async def _generate(self, state: RagChatState) -> RagChatState:
        evidence = state.get("evidence") or []
        writer = get_stream_writer()
        if not evidence:
            writer({"kind": "delta", "text": NO_EVIDENCE_ANSWER})
            return {
                "answer": NO_EVIDENCE_ANSWER,
                "citations": [],
                "finish_reason": "no_evidence",
            }

        if self.provider is None:
            raise ChatModelError(
                "问答模型尚未配置",
                code=ErrorCode.CHAT_MODEL_NOT_CONFIGURED,
            )

        messages = _build_messages(
            query=state["query"],
            history=state.get("history") or [],
            evidence=evidence,
        )
        parts: list[str] = []
        async for delta in self.provider.stream(
            messages,
            max_output_tokens=self.settings.deepseek_max_output_tokens,
        ):
            if not delta:
                continue
            parts.append(delta)
            writer({"kind": "delta", "text": delta})

        answer = "".join(parts).strip()
        citations = [
            CitationDraft.from_evidence(item, rank=index)
            for index, item in enumerate(evidence, start=1)
        ]
        for citation in citations:
            writer({"kind": "citation", "citation": citation})
        return {
            "answer": answer,
            "citations": citations,
            "finish_reason": "stop",
        }

    async def _validate(self, state: RagChatState) -> RagChatState:
        answer = (state.get("answer") or "").strip()
        if not answer:
            raise ChatModelError(
                "模型返回了空回答",
                code=ErrorCode.CHAT_EMPTY_RESPONSE,
            )
        citations = state.get("citations") or []
        if state.get("evidence") and not citations:
            raise ChatModelError("回答缺少可追溯引用")
        get_stream_writer()(
            {
                "kind": "final",
                "answer": answer,
                "citations": citations,
                "finish_reason": state.get("finish_reason") or "stop",
            }
        )
        return {"answer": answer, "citations": citations}


def _build_messages(
    *,
    query: str,
    history: Sequence[ChatMessage],
    evidence: Sequence[RetrievalEvidence],
) -> list[ChatMessage]:
    context_parts: list[str] = []
    current_length = 0
    for index, item in enumerate(evidence, start=1):
        metadata = " / ".join(
            value
            for value in (item.title, item.author_name, item.dynasty_name)
            if value
        )
        chunk = (
            f"[{index}] {metadata}\n"
            f"{item.text[:_MAX_CONTEXT_CHUNK_LENGTH]}"
        )
        if current_length + len(chunk) > _MAX_CONTEXT_LENGTH:
            break
        context_parts.append(chunk)
        current_length += len(chunk)

    system_prompt = (
        "你是中国古典诗词知识助手。只能依据提供的检索证据回答，"
        "不得编造作品、作者、朝代、典故或出处。证据不足时明确说明。"
        "回答应准确、简洁，并结合诗句说明；不要提及检索系统或内部流程。"
    )
    context = "\n\n".join(context_parts)
    messages = [ChatMessage(role="system", content=system_prompt)]
    messages.extend(history)
    messages.append(
        ChatMessage(
            role="user",
            content=f"检索证据：\n{context}\n\n用户问题：{query}",
        )
    )
    return messages
