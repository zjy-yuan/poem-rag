from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal, TypedDict, cast

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.ai.providers.chat import ChatMessage, ChatModelError, ChatModelPort
from app.core.config import Settings
from app.core.errors import ErrorCode
from app.models.chunk import ChunkGranularity
from app.schemas.retrieval import RetrievalEvidence
from app.services.query_expansion import QueryRewriter
from app.services.retrieval import EvidenceRetriever

NO_EVIDENCE_ANSWER = "没有在当前诗词库中找到足够依据，暂时无法回答这个问题。"
_MAX_CONTEXT_CHUNK_LENGTH = 1200
_MAX_CONTEXT_LENGTH = 6000
_CITATION_GROUP_PATTERN = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")

logger = logging.getLogger(__name__)


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


class EvidenceAssessment(BaseModel):
    answerable: bool
    reason_code: Literal[
        "supported",
        "missing_fact",
        "ambiguous_question",
        "insufficient_context",
    ]
    missing: list[str] = Field(default_factory=list)


class RagChatState(TypedDict, total=False):
    query: str
    history: list[ChatMessage]
    rewritten_query: str
    matched_concepts: list[str]
    matched_entities: list[str]
    evidence: list[RetrievalEvidence]
    candidate_count: int
    evidence_sufficient: bool
    assessment_status: str
    assessment_reason_code: str
    refused: bool
    answer: str
    citations: list[CitationDraft]
    finish_reason: str


class RagChatGraph:
    """Poetry RAG flow: rewrite -> retrieve -> assess -> generate|refuse -> validate."""

    def __init__(
        self,
        *,
        retrieval: EvidenceRetriever,
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
        graph.add_node("assess", self._assess)
        graph.add_node("generate", self._generate)
        graph.add_node("refuse", self._refuse)
        graph.add_node("validate", self._validate)
        graph.add_edge(START, "rewrite")
        graph.add_edge("rewrite", "retrieve")
        graph.add_edge("retrieve", "assess")
        graph.add_conditional_edges(
            "assess",
            _route_after_assessment,
            {"generate": "generate", "refuse": "refuse"},
        )
        graph.add_edge("generate", "validate")
        graph.add_edge("refuse", "validate")
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
        started_at = perf_counter()
        try:
            rewrite = await self.rewriter.rewrite(state["query"])
            variants = [state["query"], *rewrite.variants]
            return {
                "rewritten_query": " ".join(dict.fromkeys(variants)),
                "matched_concepts": list(rewrite.matched_concepts),
                "matched_entities": list(rewrite.matched_entities),
            }
        finally:
            _emit_timing("rewrite", started_at)

    async def _retrieve(self, state: RagChatState) -> RagChatState:
        started_at = perf_counter()
        try:
            # The online retrieval stack owns query expansion. Passing the
            # concatenated rewrite back into it would rewrite expanded terms again
            # and lose the structured sub-question groups.
            result = await self.retrieval.search_evidence(
                query=state["query"],
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
        finally:
            _emit_timing("retrieval", started_at)

    async def _assess(self, state: RagChatState) -> RagChatState:
        started_at = perf_counter()
        try:
            evidence = state.get("evidence") or []
            if not evidence:
                return _assessment_state(
                    answerable=False,
                    status="skipped",
                    reason_code="no_evidence",
                )
            if self.provider is None:
                return _assessment_state(
                    answerable=True,
                    status="failed_open",
                    reason_code="provider_not_configured",
                )

            try:
                raw_assessment = await self.provider.generate(
                    _build_assessment_messages(
                        query=state["query"],
                        history=state.get("history") or [],
                        evidence=evidence,
                    ),
                    max_output_tokens=self.settings.chat_assess_max_output_tokens,
                    temperature=0.0,
                    response_format="json_object",
                )
                assessment = EvidenceAssessment.model_validate_json(raw_assessment)
            except Exception as exc:
                logger.warning(
                    "Evidence assessment failed open (%s)",
                    type(exc).__name__,
                    exc_info=True,
                )
                return _assessment_state(
                    answerable=True,
                    status="failed_open",
                    reason_code="assessment_error",
                )

            return _assessment_state(
                answerable=assessment.answerable,
                status="passed" if assessment.answerable else "refused",
                reason_code=assessment.reason_code,
            )
        finally:
            _emit_timing("assess", started_at)

    async def _generate(self, state: RagChatState) -> RagChatState:
        started_at = perf_counter()
        try:
            evidence = state.get("evidence") or []
            writer = get_stream_writer()
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
            citations = _resolve_citations(answer, evidence)
            for citation in citations:
                writer({"kind": "citation", "citation": citation})
            return {
                "answer": answer,
                "citations": citations,
                "finish_reason": "stop",
                "refused": False,
            }
        finally:
            _emit_timing("generation", started_at)

    async def _refuse(self, state: RagChatState) -> RagChatState:
        started_at = perf_counter()
        try:
            get_stream_writer()({"kind": "delta", "text": NO_EVIDENCE_ANSWER})
            return {
                "answer": NO_EVIDENCE_ANSWER,
                "citations": [],
                "finish_reason": "no_evidence",
                "refused": True,
            }
        finally:
            _emit_timing("generation", started_at)

    async def _validate(self, state: RagChatState) -> RagChatState:
        started_at = perf_counter()
        try:
            answer = (state.get("answer") or "").strip()
            if not answer:
                raise ChatModelError(
                    "模型返回了空回答",
                    code=ErrorCode.CHAT_EMPTY_RESPONSE,
                )
            citations = state.get("citations") or []
            if state.get("evidence") and not citations and not state.get("refused"):
                raise ChatModelError(
                    "回答缺少可追溯引用",
                    code=ErrorCode.CHAT_CITATION_MISSING,
                )
            get_stream_writer()(
                {
                    "kind": "final",
                    "answer": answer,
                    "citations": citations,
                    "finish_reason": state.get("finish_reason") or "stop",
                }
            )
            return {"answer": answer, "citations": citations}
        finally:
            _emit_timing("validate", started_at)


def _route_after_assessment(state: RagChatState) -> str:
    return "generate" if state.get("evidence_sufficient") else "refuse"


def _emit_timing(stage: str, started_at: float) -> None:
    duration_ms = max(0.0, round((perf_counter() - started_at) * 1000, 3))
    get_stream_writer()(
        {
            "kind": "timing",
            "stage": stage,
            "duration_ms": duration_ms,
        }
    )


def _assessment_state(
    *,
    answerable: bool,
    status: str,
    reason_code: str,
) -> RagChatState:
    """Record the assessment verdict for graph routing and diagnostics.

    The ``assessment`` event is internal: the public chat stream only forwards
    the retrieval, delta, citation and final events.
    """

    get_stream_writer()(
        {
            "kind": "assessment",
            "status": status,
            "reason_code": reason_code,
        }
    )
    return {
        "evidence_sufficient": answerable,
        "assessment_status": status,
        "assessment_reason_code": reason_code,
    }


def _build_messages(
    *,
    query: str,
    history: Sequence[ChatMessage],
    evidence: Sequence[RetrievalEvidence],
) -> list[ChatMessage]:
    context = _format_evidence_context(evidence)
    system_prompt = (
        "你是中国古典诗词知识助手。只能依据提供的检索证据回答，"
        "不得编造作品、作者、朝代、典故或出处。证据不足时明确说明。"
        "回答应准确、简洁，并结合诗句说明；不要提及检索系统或内部流程。"
        "必须在回答中使用 [1]、[2] 形式标注实际使用的证据，"
        "每个事实性结论至少对应一个引用；不要引用未使用的证据。"
    )
    messages = [ChatMessage(role="system", content=system_prompt)]
    messages.extend(history)
    messages.append(
        ChatMessage(
            role="user",
            content=f"检索证据：\n{context}\n\n用户问题：{query}",
        )
    )
    return messages


def _build_assessment_messages(
    *,
    query: str,
    history: Sequence[ChatMessage],
    evidence: Sequence[RetrievalEvidence],
) -> list[ChatMessage]:
    context = _format_evidence_context(evidence)
    system_prompt = (
        "你是中国古典诗词 RAG 的可答性判定器。"
        "只判断提供的检索证据是否足以回答用户问题的核心事实，不判断话题是否相关。"
        "只能依据检索证据，不得使用外部知识。"
        "历史对话只用于理解代词和上下文，不能当作证据。"
        "如果问题包含多个并列要求、多个作品或比较多个对象，"
        "必须先拆分子问题并逐一核对证据；证据分散在多条记录中不影响可答性。"
        "只要每个子问题都能由至少一条证据直接支持，就必须判定 answerable=true。"
        "只有至少一个子问题缺少直接证据时，才判定 answerable=false，"
        "并在 missing 中列出缺少的关键信息。"
        "如果证据只命中人物、朝代或主题，却没有回答问题所问的属性，"
        "必须判定 answerable=false。"
        "请只输出 JSON 对象，不要输出 Markdown 或额外说明。"
        'JSON 格式：{"answerable": false, "reason_code": "missing_fact", '
        '"missing": ["缺失的关键信息"]}。'
        "reason_code 只能是 supported、missing_fact、ambiguous_question、"
        "insufficient_context。"
    )
    messages = [ChatMessage(role="system", content=system_prompt)]
    messages.extend(history)
    messages.append(
        ChatMessage(
            role="user",
            content=f"检索证据：\n{context}\n\n用户问题：{query}",
        )
    )
    return messages


def _format_evidence_context(
    evidence: Sequence[RetrievalEvidence],
) -> str:
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
    return "\n\n".join(context_parts)


def _resolve_citations(
    answer: str,
    evidence: Sequence[RetrievalEvidence],
) -> list[CitationDraft]:
    """Resolve only the evidence indexes that the model actually cited."""
    cited_indexes: list[int] = []
    seen: set[int] = set()
    for group in _CITATION_GROUP_PATTERN.finditer(answer):
        for raw_index in re.split(r"[,，]", group.group(1)):
            index = int(raw_index.strip())
            if index < 1 or index > len(evidence):
                raise ChatModelError(
                    f"模型返回了无效引用标记 [{index}]",
                    code=ErrorCode.CHAT_CITATION_INVALID,
                )
            if index not in seen:
                seen.add(index)
                cited_indexes.append(index)

    return [
        CitationDraft.from_evidence(evidence[index - 1], rank=index)
        for index in cited_indexes
    ]
