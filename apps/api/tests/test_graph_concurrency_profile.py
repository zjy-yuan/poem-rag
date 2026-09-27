from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "profile_graph_concurrency.py"
)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "profile_graph_concurrency",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeGraph:
    async def stream(
        self,
        *,
        query: str,
        history: Sequence[object],
    ) -> AsyncIterator[dict[str, object]]:
        del history
        await asyncio.sleep(0.01)
        yield {
            "kind": "retrieval",
            "strategy": "fake-graph-v1",
            "selected_count": 1,
        }
        yield {"kind": "timing", "stage": "retrieval", "duration_ms": 10.0}
        yield {"kind": "timing", "stage": "assess", "duration_ms": 20.0}
        yield {"kind": "timing", "stage": "generation", "duration_ms": 30.0}
        yield {"kind": "delta", "text": f"{query} [1]"}


class FakeEmbeddingProvider:
    model = "fake-embedding"
    dimension = 2

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.active_calls = 0
        self.max_active_calls = 0
        self.closed = False

    async def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        await self._run()
        return [[1.0, 0.0] for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        del text
        await self._run()
        return [1.0, 0.0]

    async def aclose(self) -> None:
        self.closed = True

    async def _run(self) -> None:
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            if self.delay > 0:
                await asyncio.sleep(self.delay)
        finally:
            self.active_calls -= 1


async def test_graph_profile_bounds_concurrency_and_preserves_order() -> None:
    module = _load_script()
    cases = module.PROFILE_CASES[:3]
    active_calls = 0
    max_active_calls = 0

    @asynccontextmanager
    async def graph_factory(case: object) -> AsyncIterator[FakeGraph]:
        nonlocal active_calls, max_active_calls
        del case
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        try:
            yield FakeGraph()
        finally:
            active_calls -= 1

    report = await module.profile_graphs(
        cases,
        graph_factory,
        model="fake-model",
        provider_mode="fake",
        retrieval_mode="fake",
        concurrency=2,
    )

    assert max_active_calls == 2
    assert report.request_count == 3
    assert report.success_count == 3
    assert report.error_count == 0
    assert report.throughput_requests_per_second > 0
    assert [result.case_id for result in report.results] == [
        case.case_id for case in cases
    ]
    assert report.average_stage_timings_ms == {
        "retrieval": 10.0,
        "assess": 20.0,
        "generation": 30.0,
    }
    assert all(result.ttft_ms is not None for result in report.results)


def test_graph_profile_parser() -> None:
    module = _load_script()
    parser = module._build_parser()

    defaults = parser.parse_args([])
    assert defaults.provider == "real"
    assert defaults.retrieval == "real"
    assert defaults.concurrency == 1
    assert defaults.dataset == module.DEFAULT_DATASET
    assert defaults.case_id is None
    assert defaults.repeat == 1
    assert defaults.variant_limit is None
    assert defaults.embedding_max_retries is None
    assert defaults.embedding_concurrency is None
    assert defaults.embedding_cache is False
    parsed = parser.parse_args(
        [
            "--provider",
            "fake",
            "--retrieval",
            "fake",
            "--concurrency",
            "4",
            "--case-id",
            "gen-v3-duange-talent",
            "--repeat",
            "2",
            "--variant-limit",
            "3",
            "--embedding-max-retries",
            "0",
            "--embedding-concurrency",
            "2",
            "--embedding-cache",
        ]
    )
    assert parsed.provider == "fake"
    assert parsed.retrieval == "fake"
    assert parsed.concurrency == 4
    assert parsed.case_id == ["gen-v3-duange-talent"]
    assert parsed.repeat == 2
    assert parsed.variant_limit == 3
    assert parsed.embedding_max_retries == 0
    assert parsed.embedding_concurrency == 2
    assert parsed.embedding_cache is True


def test_load_profile_cases_filters_and_repeats() -> None:
    module = _load_script()

    cases = module.load_profile_cases(
        module.DEFAULT_DATASET,
        case_ids=("gen-v3-duange-talent",),
        repeat=2,
    )

    assert len(cases) == 2
    assert [case.case_id for case in cases] == [
        "gen-v3-duange-talent",
        "gen-v3-duange-talent",
    ]
    assert cases[0].query.startswith("曹操《短歌行》")
    assert cases[0].evidence_title == "短歌行"


async def test_recording_embedding_provider_bounds_logical_concurrency() -> None:
    module = _load_script()
    provider = FakeEmbeddingProvider(delay=0.01)
    recorder = module.RecordingEmbeddingProvider(
        provider,
        batch_size=10,
        max_concurrency=1,
    )

    await asyncio.gather(
        recorder.embed_query("明月"),
        recorder.embed_query("杨柳"),
    )

    assert provider.max_active_calls == 1
    assert recorder.max_observed_concurrency == 1
    assert [call.kind for call in recorder.calls] == ["query", "query"]


async def test_embedding_http_records_retry_and_call_correlation() -> None:
    module = _load_script()
    from app.ai.providers.qwen_embedding import (
        QwenEmbeddingConfig,
        QwenEmbeddingProvider,
    )

    request_count = 0
    payloads: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        payload = json.loads(request.content)
        payloads.append(payload)
        if request_count == 1:
            return httpx.Response(
                500,
                json={"error": {"message": "temporary failure"}},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "index": 0,
                        "embedding": [1.0, 0.0],
                    }
                ]
            },
            request=request,
        )

    transport = module.RecordingEmbeddingTransport(
        httpx.MockTransport(handler)
    )
    client = httpx.AsyncClient(transport=transport)
    qwen_provider = QwenEmbeddingProvider(
        QwenEmbeddingConfig(
            api_key="test-key",
            base_url="https://embedding.test/v1",
            dimension=2,
            max_retries=1,
            retry_backoff_seconds=0,
        ),
        client=client,
    )
    recorder = module.RecordingEmbeddingProvider(
        qwen_provider,
        batch_size=10,
    )
    token = module._current_profile_case_id.set("case-http")
    try:
        vector = await recorder.embed_query("明月")
    finally:
        module._current_profile_case_id.reset(token)
        await client.aclose()

    report = module._build_embedding_report(recorder, transport)

    assert vector == [1.0, 0.0]
    assert len(payloads) == 2
    assert all(payload["input"] == ["明月"] for payload in payloads)
    assert report.logical_call_count == 1
    assert report.logical_error_count == 0
    assert report.http_request_count == 2
    assert report.retry_count == 1
    assert report.success_count == 1
    assert report.retryable_status_count == 1
    assert report.server_error_status_count == 1
    assert report.client_error_status_count == 0
    assert report.transport_error_count == 0
    assert report.max_observed_http_concurrency == 1
    assert report.status_counts == {"200": 1, "500": 1}
    assert {call.call_id for call in report.http_calls} == {1}
    assert {call.case_id for call in report.http_calls} == {"case-http"}
    assert {call.input_count for call in report.http_calls} == {1}
    assert len(report.slowest_http_calls) == 2


def test_embedding_cache_report_copies_stats() -> None:
    module = _load_script()
    stats = SimpleNamespace(
        hits=7,
        misses=3,
        writes=3,
        errors=1,
        hit_rate=0.7,
    )

    report = module._build_embedding_cache_report(stats)

    assert report.hits == 7
    assert report.misses == 3
    assert report.writes == 3
    assert report.errors == 1
    assert report.hit_rate == 0.7
