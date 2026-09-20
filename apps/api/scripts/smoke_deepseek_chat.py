from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


@dataclass(frozen=True, slots=True)
class ChatSmokeResult:
    model: str
    delta_count: int
    char_count: int
    elapsed_ms: float


async def run_smoke(
    *,
    prompt: str,
    max_output_tokens: int,
) -> ChatSmokeResult:
    from app.ai.providers.chat import ChatMessage
    from app.ai.providers.deepseek import create_deepseek_chat_provider
    from app.core.config import get_settings

    if not prompt.strip():
        raise ValueError("prompt 不能为空")
    if max_output_tokens <= 0:
        raise ValueError("max_output_tokens 必须大于 0")

    settings = get_settings()
    provider = create_deepseek_chat_provider(settings)
    if provider is None:
        raise RuntimeError("DeepSeek Chat Provider 未配置")

    delta_count = 0
    char_count = 0
    started_at = perf_counter()
    try:
        async for delta in provider.stream(
            [ChatMessage(role="user", content=prompt)],
            max_output_tokens=max_output_tokens,
        ):
            delta_count += 1
            char_count += len(delta)
    finally:
        await provider.aclose()

    return ChatSmokeResult(
        model=provider.model,
        delta_count=delta_count,
        char_count=char_count,
        elapsed_ms=(perf_counter() - started_at) * 1000,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Call the configured DeepSeek chat model with one short prompt."
    )
    parser.add_argument(
        "--prompt",
        default="请用一句话说明月亮在古诗中的常见意象。",
        help="Prompt to send. Default: a short question about the moon image.",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=64,
        help="Maximum output tokens. Default: 64",
    )
    args = parser.parse_args()
    if args.max_output_tokens <= 0:
        parser.error("--max-output-tokens must be greater than 0")

    try:
        result = asyncio.run(
            run_smoke(
                prompt=args.prompt,
                max_output_tokens=args.max_output_tokens,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"DeepSeek chat smoke failed: {exc}\n")

    print(
        f"DeepSeek chat smoke passed: model={result.model} "
        f"delta_count={result.delta_count} char_count={result.char_count} "
        f"elapsed_ms={result.elapsed_ms:.2f}"
    )


if __name__ == "__main__":
    main()
