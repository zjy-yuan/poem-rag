from __future__ import annotations

import argparse
import asyncio
import math
import sys
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


@dataclass(frozen=True, slots=True)
class EmbeddingSmokeResult:
    model: str
    configured_dimension: int | None
    returned_dimension: int
    vector_norm: float


async def run_smoke(*, text: str) -> EmbeddingSmokeResult:
    from app.ai.providers.qwen_embedding import create_qwen_embedding_provider
    from app.core.config import get_settings

    if not text.strip():
        raise ValueError("text 不能为空")

    settings = get_settings()
    provider = create_qwen_embedding_provider(settings)
    try:
        vector = await provider.embed_query(text)
    finally:
        await provider.aclose()

    if not vector:
        raise RuntimeError("Embedding Provider 返回空向量")
    if not all(math.isfinite(value) for value in vector):
        raise RuntimeError("Embedding Provider 返回非有限数值")

    return EmbeddingSmokeResult(
        model=provider.model,
        configured_dimension=provider.dimension,
        returned_dimension=len(vector),
        vector_norm=math.sqrt(sum(value * value for value in vector)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Call the configured Qwen embedding API with one short input."
    )
    parser.add_argument(
        "--text",
        default="明月",
        help="Text to embed. Default: 明月",
    )
    args = parser.parse_args()

    try:
        result = asyncio.run(run_smoke(text=args.text))
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"Qwen embedding smoke failed: {exc}\n")

    print(
        f"Qwen embedding smoke passed: model={result.model} "
        f"configured_dimension={result.configured_dimension} "
        f"returned_dimension={result.returned_dimension} "
        f"vector_norm={result.vector_norm:.6f}"
    )


if __name__ == "__main__":
    main()
