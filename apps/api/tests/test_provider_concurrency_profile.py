from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "profile_provider_concurrency.py"
)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "profile_provider_concurrency",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeProvider:
    model = "fake-provider"

    def __init__(self) -> None:
        self.active_calls = 0
        self.max_active_calls = 0
        self.generate_calls = 0
        self.stream_calls = 0
        self.closed = False

    async def generate(
        self,
        messages: Sequence[Any],
        *,
        max_output_tokens: int,
        temperature: float = 0.0,
        response_format: str | None = None,
    ) -> str:
        del messages, max_output_tokens, temperature, response_format
        self.generate_calls += 1
        await self._run()
        return "fake answer"

    async def stream(
        self,
        messages: Sequence[Any],
        *,
        max_output_tokens: int,
    ) -> AsyncIterator[str]:
        del messages, max_output_tokens
        self.stream_calls += 1
        await self._run()
        yield "fake "
        yield "answer"

    async def aclose(self) -> None:
        self.closed = True

    async def _run(self) -> None:
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(0.01)
        finally:
            self.active_calls -= 1


class FailingProvider(FakeProvider):
    async def generate(
        self,
        messages: Sequence[Any],
        *,
        max_output_tokens: int,
        temperature: float = 0.0,
        response_format: str | None = None,
    ) -> str:
        del messages, max_output_tokens, temperature, response_format
        self.generate_calls += 1
        raise FakeProviderError("fake provider failed")


class FakeProviderError(RuntimeError):
    code = "fake-provider-error"


async def test_provider_profile_bounds_concurrency_and_preserves_order() -> None:
    module = _load_script()
    workloads = module.build_workloads("mixed", max_output_tokens=32)
    provider = FakeProvider()

    report = await module.profile_provider(
        provider,
        workloads,
        mode="mixed",
        concurrency=2,
    )

    assert provider.max_active_calls == 2
    assert provider.generate_calls == 2
    assert provider.stream_calls == 2
    assert report.request_count == 4
    assert report.success_count == 4
    assert report.error_count == 0
    assert report.wall_time_ms > 0
    assert report.throughput_requests_per_second > 0
    assert [result.workload_id for result in report.results] == [
        workload.workload_id for workload, _ in workloads
    ]
    assert [
        result.ttft_ms
        for result in report.results
        if result.mode == "generate"
    ] == [None, None]
    assert all(
        result.ttft_ms is not None
        for result in report.results
        if result.mode == "stream"
    )


async def test_provider_profile_records_errors_without_retry() -> None:
    module = _load_script()
    workloads = [
        (
            module.ProviderWorkload(
                workload_id="generate-failure",
                mode="generate",
                prompt="return json",
                response_format="json_object",
            ),
            32,
        )
    ]
    provider = FailingProvider()

    report = await module.profile_provider(
        provider,
        workloads,
        mode="generate",
        concurrency=1,
    )

    assert provider.generate_calls == 1
    assert report.request_count == 1
    assert report.success_count == 0
    assert report.error_count == 1
    assert report.results[0].error_code == "fake-provider-error"
    assert report.results[0].error_message == "fake provider failed"


def test_provider_profile_parser() -> None:
    module = _load_script()
    parser = module._build_parser()

    defaults = parser.parse_args([])
    assert defaults.mode == "mixed"
    assert defaults.concurrency == 1
    assert defaults.max_output_tokens == 128
    assert parser.parse_args(
        ["--mode", "stream", "--concurrency", "4"]
    ).concurrency == 4
