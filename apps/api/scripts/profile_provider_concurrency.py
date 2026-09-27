from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Literal

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

if TYPE_CHECKING:
    from app.ai.providers.chat import (
        ChatModelPort,
        ChatResponseFormat,
    )

ProviderMode = Literal["generate", "stream"]
ProfileMode = Literal["generate", "stream", "mixed"]


@dataclass(frozen=True, slots=True)
class ProviderWorkload:
    workload_id: str
    mode: ProviderMode
    prompt: str
    response_format: ChatResponseFormat | None = None


@dataclass(frozen=True, slots=True)
class ProviderRequestResult:
    workload_id: str
    mode: ProviderMode
    latency_ms: float
    ttft_ms: float | None
    output_chars: int
    delta_count: int
    error_code: str | None
    error_message: str | None


@dataclass(frozen=True, slots=True)
class ProviderConcurrencyReport:
    model: str
    mode: ProfileMode
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
    results: tuple[ProviderRequestResult, ...]


def build_workloads(
    mode: ProfileMode,
    *,
    max_output_tokens: int,
) -> list[tuple[ProviderWorkload, int]]:
    assess_prompts = [
        (
            "请只输出 JSON 对象，判断“明月在古诗中常与思乡相关”"
            "是否有文本依据。"
        ),
        (
            "请只输出 JSON 对象，判断“杨柳在古诗中常用作送别意象”"
            "是否有文本依据。"
        ),
        (
            "请只输出 JSON 对象，判断“流水在古诗中常象征时光流逝”"
            "是否有文本依据。"
        ),
        (
            "请只输出 JSON 对象，判断“杜鹃在古诗中常带有悲苦色彩”"
            "是否有文本依据。"
        ),
    ]
    stream_prompts = [
        "请用三句话说明月亮在古诗中的常见意象。",
        "请用三句话说明杨柳为什么常与送别相关。",
        "请用三句话说明流水在古诗中常见的象征意义。",
        "请用三句话说明杜鹃在古诗中的常见情感色彩。",
    ]

    selected_assess_prompts = (
        assess_prompts
        if mode == "generate"
        else assess_prompts[:2]
    )
    selected_stream_prompts = (
        stream_prompts
        if mode == "stream"
        else stream_prompts[:2]
    )

    workloads: list[tuple[ProviderWorkload, int]] = []
    if mode in {"generate", "mixed"}:
        workloads.extend(
            (
                ProviderWorkload(
                    workload_id=f"assess-{index}",
                    mode="generate",
                    prompt=prompt,
                    response_format="json_object",
                ),
                max_output_tokens,
            )
            for index, prompt in enumerate(selected_assess_prompts, start=1)
        )
    if mode in {"stream", "mixed"}:
        workloads.extend(
            (
                ProviderWorkload(
                    workload_id=f"stream-{index}",
                    mode="stream",
                    prompt=prompt,
                ),
                max_output_tokens,
            )
            for index, prompt in enumerate(selected_stream_prompts, start=1)
        )
    if mode == "mixed":
        workloads.sort(key=lambda item: 0 if item[0].mode == "generate" else 1)
    return workloads


async def profile_provider(
    provider: ChatModelPort,
    workloads: Sequence[tuple[ProviderWorkload, int]],
    *,
    mode: ProfileMode,
    concurrency: int,
) -> ProviderConcurrencyReport:
    from app.ai.providers.chat import ChatMessage

    if not workloads:
        raise ValueError("workloads 不能为空")
    if concurrency < 1:
        raise ValueError("concurrency 必须大于等于 1")

    semaphore = asyncio.Semaphore(concurrency)

    async def run_workload(
        workload: ProviderWorkload,
        max_output_tokens: int,
    ) -> ProviderRequestResult:
        async with semaphore:
            started_at = perf_counter()
            ttft_ms: float | None = None
            output_chars = 0
            delta_count = 0
            error_code: str | None = None
            error_message: str | None = None
            try:
                messages = [ChatMessage(role="user", content=workload.prompt)]
                if workload.mode == "generate":
                    content = await provider.generate(
                        messages,
                        max_output_tokens=max_output_tokens,
                        temperature=0.0,
                        response_format=workload.response_format,
                    )
                    output_chars = len(content)
                else:
                    async for delta in provider.stream(
                        messages,
                        max_output_tokens=max_output_tokens,
                    ):
                        if ttft_ms is None:
                            ttft_ms = _elapsed_ms(started_at)
                        delta_count += 1
                        output_chars += len(delta)
            except Exception as exc:
                error_code = getattr(exc, "code", type(exc).__name__)
                error_message = str(exc)

            return ProviderRequestResult(
                workload_id=workload.workload_id,
                mode=workload.mode,
                latency_ms=_elapsed_ms(started_at),
                ttft_ms=ttft_ms,
                output_chars=output_chars,
                delta_count=delta_count,
                error_code=error_code,
                error_message=error_message,
            )

    started_at = perf_counter()
    results = await asyncio.gather(
        *(
            run_workload(workload, max_output_tokens)
            for workload, max_output_tokens in workloads
        )
    )
    wall_time_ms = _elapsed_ms(started_at)
    latencies = [result.latency_ms for result in results]
    ttfts = [
        result.ttft_ms
        for result in results
        if result.ttft_ms is not None
    ]
    success_count = sum(result.error_code is None for result in results)

    return ProviderConcurrencyReport(
        model=provider.model,
        mode=mode,
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
        results=tuple(results),
    )


async def run_profile(
    *,
    mode: ProfileMode,
    concurrency: int,
    max_output_tokens: int,
) -> ProviderConcurrencyReport:
    from app.ai.providers.deepseek import create_deepseek_chat_provider
    from app.core.config import get_settings

    if max_output_tokens <= 0:
        raise ValueError("max_output_tokens 必须大于 0")

    settings = get_settings()
    provider = create_deepseek_chat_provider(settings)
    if provider is None:
        raise RuntimeError("DeepSeek Chat Provider 未配置")

    workloads = build_workloads(
        mode,
        max_output_tokens=max_output_tokens,
    )
    try:
        return await profile_provider(
            provider,
            workloads,
            mode=mode,
            concurrency=concurrency,
        )
    finally:
        await provider.aclose()


def format_profile_report(report: ProviderConcurrencyReport) -> str:
    lines = [
        f"model={report.model} mode={report.mode} concurrency={report.concurrency}",
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
        "requests:",
    ]
    for result in report.results:
        lines.append(
            f"- {result.workload_id} mode={result.mode} "
            f"latency_ms={result.latency_ms:.3f} "
            f"ttft_ms={_format_optional(result.ttft_ms)} "
            f"deltas={result.delta_count} chars={result.output_chars} "
            f"error={result.error_code or 'none'}"
        )
        if result.error_message:
            lines.append(f"  message={result.error_message}")
    return "\n".join(lines)


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


def _format_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Profile the shared DeepSeek provider with bounded concurrency. "
            "This script performs real network calls and does not retry failures."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("generate", "stream", "mixed"),
        default="mixed",
        help="Provider workload mode. Default: mixed",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Maximum concurrent requests. Default: 1",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=128,
        help="Maximum output tokens per request. Default: 128",
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
    if args.max_output_tokens <= 0:
        parser.error("--max-output-tokens 必须大于 0")

    try:
        report = asyncio.run(
            run_profile(
                mode=args.mode,
                concurrency=args.concurrency,
                max_output_tokens=args.max_output_tokens,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"Provider concurrency profile failed: {exc}\n")

    print(format_profile_report(report))
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
