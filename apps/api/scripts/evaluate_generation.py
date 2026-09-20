from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DEFAULT_DATASET = PROJECT_ROOT / "data" / "eval" / "generation_rag_v1.json"

if TYPE_CHECKING:
    from app.schemas.generation_evaluation import GenerationEvaluationReport


async def run_evaluation(dataset_path: Path) -> GenerationEvaluationReport:
    from app.ai.graphs.rag import RagChatGraph
    from app.ai.providers.deepseek import create_deepseek_chat_provider
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.evaluation.generation import (
        GenerationEvaluator,
        load_generation_evaluation_dataset,
    )
    from app.services.chat import build_chat_retrieval
    from app.services.query_expansion import LexiconQueryRewriter

    dataset = load_generation_evaluation_dataset(dataset_path)
    settings = get_settings()
    provider = create_deepseek_chat_provider(settings)
    if provider is None:
        raise RuntimeError(
            "DEEPSEEK_API_KEY 未配置，无法执行真实生成层评估"
        )

    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    rewriter = LexiconQueryRewriter()

    @asynccontextmanager
    async def graph_factory() -> AsyncIterator[RagChatGraph]:
        async with session_factory() as session:
            yield RagChatGraph(
                retrieval=build_chat_retrieval(session),
                rewriter=rewriter,
                provider=provider,
                settings=settings,
            )

    try:
        evaluator = GenerationEvaluator(
            graph_factory,
            model=provider.model,
        )
        return await evaluator.evaluate(dataset)
    finally:
        await provider.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate the online RAG graph with a fixed generation dataset. "
            "This command uses the real configured Chat Provider."
        )
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"Evaluation dataset path. Default: {DEFAULT_DATASET}",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path for the full JSON report.",
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(run_evaluation(args.dataset))
    except RuntimeError as exc:
        parser.error(str(exc))

    from app.evaluation.generation import format_generation_evaluation_report

    print(format_generation_evaluation_report(report))
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
        print(f"\njson_report={args.json_output}")


if __name__ == "__main__":
    main()
