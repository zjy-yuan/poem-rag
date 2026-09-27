from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import math
import statistics
import sys
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal

import httpx

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DEFAULT_DATASET = (
    PROJECT_ROOT / "data" / "eval" / "generation_holdout_1000_v3.json"
)

if TYPE_CHECKING:
    from app.ai.graphs.rag import RagChatGraph
    from app.ai.providers.chat import ChatModelPort
    from app.ai.providers.embedding_cache import EmbeddingCacheStats

ProviderMode = Literal["real", "fake"]
RetrievalMode = Literal["real", "fake"]
EmbeddingCallKind = Literal["documents", "query"]

_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_current_profile_case_id: contextvars.ContextVar[str | None] = (
    contextvars.ContextVar("profile_graph_case_id", default=None)
)
_current_embedding_call_id: contextvars.ContextVar[int | None] = (
    contextvars.ContextVar("profile_graph_embedding_call_id", default=None)
)


@dataclass(frozen=True, slots=True)
class GraphProfileCase:
    case_id: str
    query: str
    evidence_title: str
    evidence_text: str


GraphFactory = Callable[
    [GraphProfileCase],
    AbstractAsyncContextManager["RagChatGraph"],
]


PROFILE_CASES = (
    GraphProfileCase(
        case_id="profile-duange",
        query="曹操《短歌行》怎样从人生短促写到求贤若渴？",
        evidence_title="短歌行",
        evidence_text=(
            "对酒当歌，人生几何！譬如朝露，去日苦多。"
            "山不厌高，海不厌深。周公吐哺，天下归心。"
        ),
    ),
    GraphProfileCase(
        case_id="profile-guancanghai",
        query="曹操《观沧海》怎样由眼前海景写到宇宙气象和志向？",
        evidence_title="观沧海",
        evidence_text=(
            "水何澹澹，山岛竦峙。秋风萧瑟，洪波涌起。"
            "日月之行，若出其中；星汉灿烂，若出其里。"
        ),
    ),
    GraphProfileCase(
        case_id="profile-wangyue",
        query="杜甫《望岳》如何描写泰山并表达登临绝顶的雄心？",
        evidence_title="望岳",
        evidence_text=(
            "造化钟神秀，阴阳割昏晓。荡胸生曾云，决眦入归鸟。"
            "会当凌绝顶，一览众山小。"
        ),
    ),
    GraphProfileCase(
        case_id="profile-shizhi",
        query="王维《使至塞上》如何写边塞行程和壮阔景色？",
        evidence_title="使至塞上",
        evidence_text=(
            "单车欲问边，属国过居延。征蓬出汉塞，归雁入胡天。"
            "大漠孤烟直，长河落日圆。"
        ),
    ),
)


def load_profile_cases(
    dataset_path: Path,
    *,
    case_ids: Sequence[str] = (),
    repeat: int = 1,
) -> tuple[GraphProfileCase, ...]:
    if repeat < 1:
        raise ValueError("repeat 必须大于等于 1")

    from app.evaluation.generation import (
        load_generation_evaluation_dataset,
    )

    dataset = load_generation_evaluation_dataset(dataset_path)
    selected_ids = set(case_ids)
    cases = tuple(
        _as_profile_case(case)
        for case in dataset.cases
        if not selected_ids or case.id in selected_ids
    )
    loaded_ids = {case.case_id for case in cases}
    missing_ids = sorted(selected_ids.difference(loaded_ids))
    if missing_ids:
        raise ValueError(
            "数据集中不存在指定的 case id: " + ", ".join(missing_ids)
        )
    if not cases:
        raise ValueError(f"数据集没有可用样本: {dataset_path}")

    return tuple(
        case
        for _ in range(repeat)
        for case in cases
    )


def _as_profile_case(case: Any) -> GraphProfileCase:
    title = next(
        (
            selector.poem_title
            for selector in case.expected_citations
            if selector.poem_title
        ),
        "dataset-evidence",
    )
    evidence_text = next(
        (
            selector.text_contains
            for selector in case.expected_citations
            if selector.text_contains
        ),
        title,
    )
    return GraphProfileCase(
        case_id=case.id,
        query=case.question,
        evidence_title=title,
        evidence_text=evidence_text,
    )


@dataclass(frozen=True, slots=True)
class GraphCaseResult:
    case_id: str
    latency_ms: float
    ttft_ms: float | None
    strategy: str | None
    selected_count: int
    error_code: str | None
    stage_timings_ms: dict[str, float]


@dataclass(frozen=True, slots=True)
class EmbeddingLogicalCall:
    call_id: int
    case_id: str | None
    kind: EmbeddingCallKind
    input_count: int
    expected_http_calls: int
    elapsed_ms: float
    error_type: str | None


@dataclass(frozen=True, slots=True)
class EmbeddingHttpCall:
    sequence: int
    call_id: int | None
    case_id: str | None
    input_count: int
    started_offset_ms: float
    elapsed_ms: float
    status_code: int | None
    error_type: str | None


@dataclass(frozen=True, slots=True)
class EmbeddingConcurrencyReport:
    logical_call_count: int
    logical_error_count: int
    max_observed_logical_concurrency: int
    http_request_count: int
    retry_count: int
    success_count: int
    retryable_status_count: int
    client_error_status_count: int
    server_error_status_count: int
    transport_error_count: int
    max_observed_http_concurrency: int
    status_counts: dict[str, int]
    average_http_latency_ms: float
    p95_http_latency_ms: float
    max_http_latency_ms: float
    slowest_http_calls: tuple[EmbeddingHttpCall, ...]
    logical_calls: tuple[EmbeddingLogicalCall, ...]
    http_calls: tuple[EmbeddingHttpCall, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingCacheReport:
    hits: int
    misses: int
    writes: int
    errors: int
    hit_rate: float


@dataclass(frozen=True, slots=True)
class GraphConcurrencyReport:
    model: str
    provider_mode: ProviderMode
    retrieval_mode: RetrievalMode
    concurrency: int
    request_count: int
    success_count: int
    error_count: int
    wall_time_ms: float
    throughput_requests_per_second: float
    average_latency_ms: float
    p95_latency_ms: float
    average_ttft_ms: float | None
    p95_ttft_ms: float | None
    average_stage_timings_ms: dict[str, float]
    p95_stage_timings_ms: dict[str, float]
    results: tuple[GraphCaseResult, ...]
    dataset: str | None = None
    repeat_count: int = 1
    variant_limit: int | None = None
    embedding_max_retries: int | None = None
    embedding_concurrency: int | None = None
    embedding: EmbeddingConcurrencyReport | None = None
    embedding_cache_enabled: bool = False
    embedding_cache: EmbeddingCacheReport | None = None


class RecordingEmbeddingTransport(httpx.AsyncBaseTransport):
    """Record physical Embedding HTTP calls without retaining request bodies."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._transport = transport or httpx.AsyncHTTPTransport()
        self.calls: list[EmbeddingHttpCall] = []
        self._started_at = perf_counter()
        self._sequence = 0
        self._active_calls = 0
        self.max_observed_concurrency = 0

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        sequence = self._sequence
        self._sequence += 1
        call_id = _current_embedding_call_id.get()
        case_id = _current_profile_case_id.get()
        input_count = _request_input_count(request)
        started_offset_ms = _elapsed_ms(self._started_at)
        started_at = perf_counter()
        self._active_calls += 1
        self.max_observed_concurrency = max(
            self.max_observed_concurrency,
            self._active_calls,
        )
        status_code: int | None = None
        error_type: str | None = None
        try:
            response = await self._transport.handle_async_request(request)
            status_code = response.status_code
            return response
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            self.calls.append(
                EmbeddingHttpCall(
                    sequence=sequence,
                    call_id=call_id,
                    case_id=case_id,
                    input_count=input_count,
                    started_offset_ms=started_offset_ms,
                    elapsed_ms=_elapsed_ms(started_at),
                    status_code=status_code,
                    error_type=error_type,
                )
            )
            self._active_calls -= 1

    async def aclose(self) -> None:
        await self._transport.aclose()


class RecordingEmbeddingProvider:
    """Observe logical Embedding calls and optionally cap their concurrency."""

    def __init__(
        self,
        provider: Any,
        *,
        batch_size: int,
        max_concurrency: int | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size 必须大于等于 1")
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("max_concurrency 必须大于等于 1")
        self.provider = provider
        self.batch_size = batch_size
        self.max_concurrency = max_concurrency
        self.calls: list[EmbeddingLogicalCall] = []
        self._next_call_id = 1
        self._semaphore = (
            asyncio.Semaphore(max_concurrency)
            if max_concurrency is not None
            else None
        )
        self._active_calls = 0
        self.max_observed_concurrency = 0

    @property
    def model(self) -> str:
        return self.provider.model

    @property
    def dimension(self) -> int | None:
        return self.provider.dimension

    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        expected_http_calls = (
            math.ceil(len(texts) / self.batch_size) if texts else 0
        )
        return await self._run_call(
            kind="documents",
            input_count=len(texts),
            expected_http_calls=expected_http_calls,
            operation=lambda: self.provider.embed_documents(texts),
        )

    async def embed_query(self, text: str) -> list[float]:
        return await self._run_call(
            kind="query",
            input_count=1,
            expected_http_calls=1,
            operation=lambda: self.provider.embed_query(text),
        )

    async def aclose(self) -> None:
        await self.provider.aclose()

    async def _run_call(
        self,
        *,
        kind: EmbeddingCallKind,
        input_count: int,
        expected_http_calls: int,
        operation: Callable[[], Any],
    ) -> Any:
        call_id = self._next_call_id
        self._next_call_id += 1
        case_id = _current_profile_case_id.get()
        token = _current_embedding_call_id.set(call_id)
        started_at = perf_counter()
        error_type: str | None = None

        async def run_operation() -> Any:
            self._active_calls += 1
            self.max_observed_concurrency = max(
                self.max_observed_concurrency,
                self._active_calls,
            )
            try:
                return await operation()
            finally:
                self._active_calls -= 1

        try:
            if self._semaphore is None:
                return await run_operation()
            async with self._semaphore:
                return await run_operation()
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            _current_embedding_call_id.reset(token)
            self.calls.append(
                EmbeddingLogicalCall(
                    call_id=call_id,
                    case_id=case_id,
                    kind=kind,
                    input_count=input_count,
                    expected_http_calls=expected_http_calls,
                    elapsed_ms=_elapsed_ms(started_at),
                    error_type=error_type,
                )
            )


class FakeGraphProvider:
    model = "fake-graph-provider"

    async def generate(
        self,
        messages: Sequence[Any],
        *,
        max_output_tokens: int,
        temperature: float = 0.0,
        response_format: str | None = None,
    ) -> str:
        del messages, max_output_tokens, temperature, response_format
        await asyncio.sleep(0.02)
        return (
            '{"answerable": true, "reason_code": "supported", '
            '"missing": []}'
        )

    async def stream(
        self,
        messages: Sequence[Any],
        *,
        max_output_tokens: int,
    ) -> AsyncIterator[str]:
        del messages, max_output_tokens
        await asyncio.sleep(0.02)
        yield "根据检索证据 [1]，"
        await asyncio.sleep(0.02)
        yield "这首作品围绕问题中的核心意象展开。"
        await asyncio.sleep(0.02)
        yield "固定回答用于并发诊断。"

    async def aclose(self) -> None:
        return None


class FakeGraphRetrieval:
    def __init__(self, case: GraphProfileCase) -> None:
        self.case = case

    async def search_evidence(
        self,
        *,
        query: str,
        limit: int,
        granularities: list[Any] | None = None,
        author_id: int | None = None,
        dynasty_id: int | None = None,
    ) -> Any:
        from app.models.chunk import ChunkGranularity
        from app.schemas.retrieval import RetrievalEvidence
        from app.services.retrieval import RetrievalSearchResult

        del query, limit, granularities, author_id, dynasty_id
        evidence = RetrievalEvidence(
            chunk_id=1,
            poem_id=1,
            poem_version_id=1,
            annotation_id=None,
            annotation_type=None,
            title=self.case.evidence_title,
            author_id=None,
            author_name=None,
            dynasty_id=None,
            dynasty_name=None,
            granularity=ChunkGranularity.POEM,
            chunk_index=0,
            text=self.case.evidence_text,
            line_start=1,
            line_end=4,
            chunk_strategy="fake-profile",
            status="published",
            score=1.0,
            match_types=["fake_profile"],
            published_at=None,
        )
        await asyncio.sleep(0.001)
        return RetrievalSearchResult(
            items=[evidence],
            strategy="fake-profile-retrieval-v1",
            normalized_query=self.case.query,
            candidate_count=1,
        )

    async def aclose(self) -> None:
        return None


async def profile_graphs(
    cases: Sequence[GraphProfileCase],
    graph_factory: GraphFactory,
    *,
    model: str,
    provider_mode: ProviderMode,
    retrieval_mode: RetrievalMode,
    concurrency: int,
) -> GraphConcurrencyReport:
    if not cases:
        raise ValueError("cases 不能为空")
    if concurrency < 1:
        raise ValueError("concurrency 必须大于等于 1")

    semaphore = asyncio.Semaphore(concurrency)

    async def run_case(case: GraphProfileCase) -> GraphCaseResult:
        async with semaphore:
            token = _current_profile_case_id.set(case.case_id)
            try:
                return await _profile_case(case, graph_factory)
            finally:
                _current_profile_case_id.reset(token)

    started_at = perf_counter()
    results = await asyncio.gather(*(run_case(case) for case in cases))
    wall_time_ms = _elapsed_ms(started_at)
    latencies = [result.latency_ms for result in results]
    ttfts = [
        result.ttft_ms
        for result in results
        if result.ttft_ms is not None
    ]
    success_count = sum(result.error_code is None for result in results)
    stage_timings = _collect_stage_timings(results)

    return GraphConcurrencyReport(
        model=model,
        provider_mode=provider_mode,
        retrieval_mode=retrieval_mode,
        concurrency=concurrency,
        request_count=len(results),
        success_count=success_count,
        error_count=len(results) - success_count,
        wall_time_ms=wall_time_ms,
        throughput_requests_per_second=_throughput(
            request_count=len(results),
            wall_time_ms=wall_time_ms,
        ),
        average_latency_ms=_rounded_mean(latencies),
        p95_latency_ms=_p95(latencies),
        average_ttft_ms=_rounded_mean(ttfts) if ttfts else None,
        p95_ttft_ms=_p95(ttfts) if ttfts else None,
        average_stage_timings_ms={
            stage: _rounded_mean(values)
            for stage, values in stage_timings.items()
        },
        p95_stage_timings_ms={
            stage: _p95(values)
            for stage, values in stage_timings.items()
        },
        results=tuple(results),
    )


async def run_profile(
    *,
    provider_mode: ProviderMode,
    retrieval_mode: RetrievalMode,
    concurrency: int,
    dataset_path: Path = DEFAULT_DATASET,
    case_ids: Sequence[str] = (),
    repeat: int = 1,
    variant_limit: int | None = None,
    embedding_max_retries: int | None = None,
    embedding_concurrency: int | None = None,
    embedding_cache: bool = False,
) -> GraphConcurrencyReport:
    from app.ai.graphs.rag import RagChatGraph
    from app.ai.providers.deepseek import create_deepseek_chat_provider
    from app.ai.providers.qdrant import create_qdrant_vector_store
    from app.ai.providers.qwen_embedding import (
        create_qwen_embedding_provider,
    )
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.services.chat import (
        ChatRetrievalResources,
        build_chat_retrieval,
    )
    from app.services.query_expansion import LexiconQueryRewriter

    if concurrency < 1:
        raise ValueError("concurrency 必须大于等于 1")
    if repeat < 1:
        raise ValueError("repeat 必须大于等于 1")
    if variant_limit is not None and variant_limit < 1:
        raise ValueError("variant_limit 必须大于等于 1")
    if embedding_max_retries is not None and embedding_max_retries < 0:
        raise ValueError("embedding_max_retries 不能小于 0")
    if embedding_concurrency is not None and embedding_concurrency < 1:
        raise ValueError("embedding_concurrency 必须大于等于 1")
    if embedding_cache and retrieval_mode != "real":
        raise ValueError("embedding_cache 只能与真实检索模式一起使用")

    settings = get_settings()
    settings_updates: dict[str, int] = {}
    if variant_limit is not None:
        settings_updates["chat_query_variant_limit"] = variant_limit
    if embedding_max_retries is not None:
        settings_updates["qwen_embedding_max_retries"] = embedding_max_retries
    if settings_updates:
        settings = settings.model_copy(update=settings_updates)

    cases = load_profile_cases(
        dataset_path,
        case_ids=case_ids,
        repeat=repeat,
    )

    real_provider: ChatModelPort | None = None
    engine = None
    resources = None
    embedding_client: httpx.AsyncClient | None = None
    embedding_transport: RecordingEmbeddingTransport | None = None
    recording_embedding_provider: RecordingEmbeddingProvider | None = None
    cached_embedding_provider: Any | None = None
    provider: ChatModelPort

    try:
        if provider_mode == "real":
            real_provider = create_deepseek_chat_provider(settings)
            if real_provider is None:
                raise RuntimeError("DeepSeek Chat Provider 未配置")
            provider = real_provider
        else:
            provider = FakeGraphProvider()

        if retrieval_mode == "real":
            if not settings.qdrant_url or settings.dashscope_api_key is None:
                raise RuntimeError(
                    "真实检索所需的 Qdrant 或 Qwen Embedding 未配置"
                )
            embedding_transport = RecordingEmbeddingTransport()
            embedding_client = httpx.AsyncClient(
                transport=embedding_transport,
                timeout=settings.qwen_embedding_timeout_seconds,
            )
            qwen_provider = create_qwen_embedding_provider(
                settings,
                client=embedding_client,
            )
            embedding_provider: Any = qwen_provider
            if embedding_cache:
                if not settings.redis_url:
                    raise RuntimeError(
                        "Embedding 缓存需要配置 REDIS_URL"
                    )
                from app.ai.providers.embedding_cache import (
                    CachedEmbeddingProvider,
                    RedisEmbeddingCache,
                )

                cached_embedding_provider = CachedEmbeddingProvider(
                    qwen_provider,
                    cache=RedisEmbeddingCache(
                        settings.redis_url,
                        timeout_seconds=(
                            settings.embedding_cache_timeout_seconds
                        ),
                    ),
                    ttl_seconds=settings.embedding_cache_ttl_seconds,
                )
                embedding_provider = cached_embedding_provider
            recording_embedding_provider = RecordingEmbeddingProvider(
                embedding_provider,
                batch_size=settings.qwen_embedding_batch_size,
                max_concurrency=embedding_concurrency,
            )
            resources = ChatRetrievalResources(
                embedding_provider=recording_embedding_provider,
                vector_store=create_qdrant_vector_store(settings),
            )
            engine = create_database_engine(settings)
            session_factory = create_session_factory(engine)
            rewriter = LexiconQueryRewriter()

        @asynccontextmanager
        async def graph_factory(
            case: GraphProfileCase,
        ) -> AsyncIterator[RagChatGraph]:
            if retrieval_mode == "fake":
                fake_retrieval = FakeGraphRetrieval(case)
                try:
                    yield RagChatGraph(
                        retrieval=fake_retrieval,
                        rewriter=LexiconQueryRewriter(),
                        provider=provider,
                        settings=settings,
                    )
                finally:
                    await fake_retrieval.aclose()
                return

            if engine is None or resources is None:
                raise RuntimeError("真实检索资源尚未初始化")
            async with session_factory() as session:
                retrieval = await build_chat_retrieval(
                    session,
                    settings,
                    resources=resources,
                )
                try:
                    yield RagChatGraph(
                        retrieval=retrieval,
                        rewriter=rewriter,
                        provider=provider,
                        settings=settings,
                    )
                finally:
                    await retrieval.aclose()

        report = await profile_graphs(
            cases,
            graph_factory,
            model=provider.model,
            provider_mode=provider_mode,
            retrieval_mode=retrieval_mode,
            concurrency=concurrency,
        )
        embedding_report = (
            _build_embedding_report(
                recording_embedding_provider,
                embedding_transport,
            )
            if (
                recording_embedding_provider is not None
                and embedding_transport is not None
            )
            else None
        )
        return replace(
            report,
            dataset=dataset_path.name,
            repeat_count=repeat,
            variant_limit=settings.chat_query_variant_limit,
            embedding_max_retries=settings.qwen_embedding_max_retries,
            embedding_concurrency=embedding_concurrency,
            embedding=embedding_report,
            embedding_cache_enabled=embedding_cache,
            embedding_cache=(
                _build_embedding_cache_report(
                    cached_embedding_provider.stats
                )
                if cached_embedding_provider is not None
                else None
            ),
        )
    finally:
        if resources is not None:
            await resources.aclose()
        if embedding_client is not None:
            await embedding_client.aclose()
        if real_provider is not None:
            await real_provider.aclose()
        if engine is not None:
            await engine.dispose()


async def _profile_case(
    case: GraphProfileCase,
    graph_factory: GraphFactory,
) -> GraphCaseResult:
    started_at = perf_counter()
    ttft_ms: float | None = None
    strategy: str | None = None
    selected_count = 0
    error_code: str | None = None
    stage_timings: dict[str, float] = {}

    try:
        async with graph_factory(case) as graph:
            async for event in graph.stream(query=case.query, history=[]):
                kind = event.get("kind")
                if kind == "retrieval":
                    raw_strategy = event.get("strategy")
                    if isinstance(raw_strategy, str) and raw_strategy:
                        strategy = raw_strategy
                    selected_count = _safe_int(event.get("selected_count"))
                elif kind == "timing":
                    stage = event.get("stage")
                    duration_ms = event.get("duration_ms")
                    if isinstance(stage, str) and _is_number(duration_ms):
                        stage_timings[stage] = round(
                            max(0.0, float(duration_ms)),
                            3,
                        )
                elif kind == "delta" and ttft_ms is None:
                    text = event.get("text")
                    if isinstance(text, str) and text:
                        ttft_ms = _elapsed_ms(started_at)
    except Exception as exc:
        error_code = getattr(exc, "code", type(exc).__name__)

    return GraphCaseResult(
        case_id=case.case_id,
        latency_ms=_elapsed_ms(started_at),
        ttft_ms=ttft_ms,
        strategy=strategy,
        selected_count=selected_count,
        error_code=error_code,
        stage_timings_ms=stage_timings,
    )


def _build_embedding_report(
    provider: RecordingEmbeddingProvider,
    transport: RecordingEmbeddingTransport,
) -> EmbeddingConcurrencyReport:
    logical_calls = tuple(provider.calls)
    http_calls = tuple(transport.calls)
    status_counts: dict[str, int] = {}
    for call in http_calls:
        key = (
            str(call.status_code)
            if call.status_code is not None
            else call.error_type or "unknown"
        )
        status_counts[key] = status_counts.get(key, 0) + 1

    latencies = [call.elapsed_ms for call in http_calls]
    expected_http_calls = {
        call.call_id: call.expected_http_calls
        for call in logical_calls
    }
    observed_http_calls: dict[int, int] = {}
    orphan_http_calls = 0
    for call in http_calls:
        if call.call_id is None:
            orphan_http_calls += 1
        else:
            observed_http_calls[call.call_id] = (
                observed_http_calls.get(call.call_id, 0) + 1
            )
    retry_count = sum(
        max(0, observed - expected_http_calls.get(call_id, 0))
        for call_id, observed in observed_http_calls.items()
    )
    retry_count += orphan_http_calls

    return EmbeddingConcurrencyReport(
        logical_call_count=len(logical_calls),
        logical_error_count=sum(
            call.error_type is not None for call in logical_calls
        ),
        max_observed_logical_concurrency=provider.max_observed_concurrency,
        http_request_count=len(http_calls),
        retry_count=retry_count,
        success_count=sum(
            call.status_code is not None
            and 200 <= call.status_code < 400
            for call in http_calls
        ),
        retryable_status_count=sum(
            call.status_code in _RETRYABLE_STATUS_CODES
            for call in http_calls
        ),
        client_error_status_count=sum(
            call.status_code is not None
            and 400 <= call.status_code < 500
            for call in http_calls
        ),
        server_error_status_count=sum(
            call.status_code is not None
            and 500 <= call.status_code < 600
            for call in http_calls
        ),
        transport_error_count=sum(
            call.error_type is not None for call in http_calls
        ),
        max_observed_http_concurrency=transport.max_observed_concurrency,
        status_counts=status_counts,
        average_http_latency_ms=_rounded_mean(latencies),
        p95_http_latency_ms=_p95(latencies),
        max_http_latency_ms=round(max(latencies), 3) if latencies else 0.0,
        slowest_http_calls=tuple(
            sorted(
                http_calls,
                key=lambda call: call.elapsed_ms,
                reverse=True,
            )[:5]
        ),
        logical_calls=logical_calls,
        http_calls=http_calls,
    )


def _build_embedding_cache_report(
    stats: EmbeddingCacheStats,
) -> EmbeddingCacheReport:
    return EmbeddingCacheReport(
        hits=stats.hits,
        misses=stats.misses,
        writes=stats.writes,
        errors=stats.errors,
        hit_rate=stats.hit_rate,
    )


def format_graph_report(report: GraphConcurrencyReport) -> str:
    lines = [
        (
            f"model={report.model} retrieval={report.retrieval_mode} "
            f"provider={report.provider_mode} concurrency={report.concurrency} "
            f"repeat={report.repeat_count}"
        ),
        (
            f"dataset={report.dataset or 'n/a'} "
            f"variant_limit={report.variant_limit or 'n/a'} "
            f"embedding_max_retries="
            f"{_format_int_optional(report.embedding_max_retries)} "
            f"embedding_concurrency="
            f"{report.embedding_concurrency or 'unbounded'} "
            f"embedding_cache="
            f"{'enabled' if report.embedding_cache_enabled else 'disabled'}"
        ),
        (
            f"requests={report.request_count} success={report.success_count} "
            f"errors={report.error_count}"
        ),
        (
            f"wall_time_ms={report.wall_time_ms:.3f} "
            "throughput_requests_per_second="
            f"{report.throughput_requests_per_second:.3f}"
        ),
        (
            f"average_latency_ms={report.average_latency_ms:.3f} "
            f"p95_latency_ms={report.p95_latency_ms:.3f}"
        ),
        (
            "average_ttft_ms="
            f"{_format_optional(report.average_ttft_ms)} "
            f"p95_ttft_ms={_format_optional(report.p95_ttft_ms)}"
        ),
        f"average_stage_timings_ms={_format_timings(report.average_stage_timings_ms)}",
        f"p95_stage_timings_ms={_format_timings(report.p95_stage_timings_ms)}",
    ]
    if report.embedding_cache is not None:
        embedding_cache = report.embedding_cache
        lines.append(
            "embedding_cache "
            f"hits={embedding_cache.hits} "
            f"misses={embedding_cache.misses} "
            f"writes={embedding_cache.writes} "
            f"errors={embedding_cache.errors} "
            f"hit_rate={embedding_cache.hit_rate:.6f}"
        )
    if report.embedding is not None:
        embedding = report.embedding
        lines.extend(
            [
                (
                    f"embedding_logical_calls={embedding.logical_call_count} "
                    f"errors={embedding.logical_error_count} "
                    f"max_concurrency="
                    f"{embedding.max_observed_logical_concurrency}"
                ),
                (
                    f"embedding_http_requests={embedding.http_request_count} "
                    f"retries={embedding.retry_count} "
                    f"success={embedding.success_count} "
                    f"retryable_status={embedding.retryable_status_count} "
                    f"client_error={embedding.client_error_status_count} "
                    f"server_error={embedding.server_error_status_count} "
                    f"transport_error={embedding.transport_error_count} "
                    f"max_http_concurrency="
                    f"{embedding.max_observed_http_concurrency}"
                ),
                (
                    "embedding_http_latency_ms "
                    f"average={embedding.average_http_latency_ms:.3f} "
                    f"p95={embedding.p95_http_latency_ms:.3f} "
                    f"max={embedding.max_http_latency_ms:.3f}"
                ),
                (
                    "embedding_status_counts="
                    f"{_format_counts(embedding.status_counts)}"
                ),
                "embedding_slowest_http_calls:",
            ]
        )
        for call in embedding.slowest_http_calls:
            lines.append(
                f"- sequence={call.sequence} call_id={call.call_id or 'n/a'} "
                f"case={call.case_id or 'n/a'} "
                f"inputs={call.input_count} "
                f"started_offset_ms={call.started_offset_ms:.3f} "
                f"elapsed_ms={call.elapsed_ms:.3f} "
                f"status={call.status_code if call.status_code is not None else 'n/a'} "
                f"error={call.error_type or 'none'}"
            )
    lines.append("cases:")
    for result in report.results:
        lines.append(
            f"- {result.case_id} latency_ms={result.latency_ms:.3f} "
            f"ttft_ms={_format_optional(result.ttft_ms)} "
            f"selected={result.selected_count} "
            f"strategy={result.strategy or 'n/a'} "
            f"error={result.error_code or 'none'} "
            f"stages={_format_timings(result.stage_timings_ms)}"
        )
    return "\n".join(lines)


def _collect_stage_timings(
    results: Sequence[GraphCaseResult],
) -> dict[str, list[float]]:
    collected: dict[str, list[float]] = {}
    for result in results:
        for stage, duration_ms in result.stage_timings_ms.items():
            collected.setdefault(stage, []).append(duration_ms)
    return collected


def _elapsed_ms(started_at: float) -> float:
    return round(max(0.0, (perf_counter() - started_at) * 1000), 3)


def _throughput(*, request_count: int, wall_time_ms: float) -> float:
    if wall_time_ms <= 0:
        return 0.0
    return round(request_count / (wall_time_ms / 1000), 3)


def _rounded_mean(values: Sequence[float]) -> float:
    return round(statistics.fmean(values), 3) if values else 0.0


def _p95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return round(ordered[index], 3)


def _safe_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _request_input_count(request: httpx.Request) -> int:
    try:
        payload = json.loads(request.content)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    inputs = payload.get("input")
    if not isinstance(inputs, list):
        return 0
    return len(inputs)


def _is_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _format_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _format_int_optional(value: int | None) -> str:
    return "n/a" if value is None else str(value)


def _format_timings(timings: dict[str, float]) -> str:
    if not timings:
        return "n/a"
    return ", ".join(
        f"{stage}={duration_ms:.3f}"
        for stage, duration_ms in sorted(timings.items())
    )


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "n/a"
    return ", ".join(
        f"{key}={value}"
        for key, value in sorted(counts.items())
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Profile RagChatGraph concurrency while independently replacing "
            "the real retrieval or chat provider with deterministic fakes."
        )
    )
    parser.add_argument(
        "--provider",
        choices=("real", "fake"),
        default="real",
        help="Chat provider implementation. Default: real",
    )
    parser.add_argument(
        "--retrieval",
        choices=("real", "fake"),
        default="real",
        help="Retrieval implementation. Default: real",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Maximum concurrent graph cases. Default: 1",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"Generation dataset path. Default: {DEFAULT_DATASET}",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=None,
        help="Profile only the selected case id; repeat for multiple ids.",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Replay the selected dataset this many times. Default: 1",
    )
    parser.add_argument(
        "--variant-limit",
        type=int,
        default=None,
        help="Temporarily override chat_query_variant_limit.",
    )
    parser.add_argument(
        "--embedding-max-retries",
        type=int,
        default=None,
        help="Temporarily override qwen_embedding_max_retries.",
    )
    parser.add_argument(
        "--embedding-concurrency",
        type=int,
        default=None,
        help="Optional in-process cap on concurrent Embedding logical calls.",
    )
    parser.add_argument(
        "--embedding-cache",
        action="store_true",
        help="Enable exact Redis Embedding cache for real retrieval mode.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path for the full JSON report.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency 必须大于等于 1")
    if args.repeat < 1:
        parser.error("--repeat 必须大于等于 1")
    if args.variant_limit is not None and args.variant_limit < 1:
        parser.error("--variant-limit 必须大于等于 1")
    if (
        args.embedding_max_retries is not None
        and args.embedding_max_retries < 0
    ):
        parser.error("--embedding-max-retries 不能小于 0")
    if (
        args.embedding_concurrency is not None
        and args.embedding_concurrency < 1
    ):
        parser.error("--embedding-concurrency 必须大于等于 1")

    try:
        report = asyncio.run(
            run_profile(
                provider_mode=args.provider,
                retrieval_mode=args.retrieval,
                concurrency=args.concurrency,
                dataset_path=args.dataset,
                case_ids=tuple(args.case_id or ()),
                repeat=args.repeat,
                variant_limit=args.variant_limit,
                embedding_max_retries=args.embedding_max_retries,
                embedding_concurrency=args.embedding_concurrency,
                embedding_cache=args.embedding_cache,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"Graph concurrency profile failed: {exc}\n")

    print(format_graph_report(report))
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(
                asdict(report),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\njson_output={args.json_output}")


if __name__ == "__main__":
    main()
