from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

DEFAULT_DATASET = PROJECT_ROOT / "data" / "eval" / "retrieval_lexical_v1.json"

if TYPE_CHECKING:
    from app.schemas.evaluation import RetrievalEvaluationReport


async def run_evaluation(
    dataset_path: Path,
    top_k: int,
    *,
    strategy: str,
    min_score: float = 0.0,
) -> RetrievalEvaluationReport:
    from app.ai.providers.qdrant import create_qdrant_vector_store
    from app.ai.providers.qwen_embedding import create_qwen_embedding_provider
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.evaluation.retrieval import (
        RetrievalEvaluator,
        RetrievalSearchPort,
        load_evaluation_dataset,
    )
    from app.services.dense_retrieval import DenseRetrievalService
    from app.services.hybrid_retrieval import HybridRetrievalService
    from app.services.query_expansion import (
        ExpandedRetrievalService,
        LexiconQueryRewriter,
    )
    from app.services.retrieval import RetrievalService

    dataset = load_evaluation_dataset(dataset_path)
    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    vector_store = None
    embedding_provider = None
    try:
        async with session_factory() as session:
            retrieval: RetrievalSearchPort
            if strategy == "lexical":
                retrieval = RetrievalService(session)
            elif strategy == "dense":
                embedding_provider = create_qwen_embedding_provider(settings)
                vector_store = create_qdrant_vector_store(settings)
                retrieval = DenseRetrievalService(
                    session,
                    embedding_provider=embedding_provider,
                    vector_store=vector_store,
                    min_score=min_score,
                )
            elif strategy == "expanded":
                retrieval = ExpandedRetrievalService(
                    RetrievalService(session),
                    LexiconQueryRewriter(),
                )
            else:
                embedding_provider = create_qwen_embedding_provider(settings)
                vector_store = create_qdrant_vector_store(settings)
                retrieval = HybridRetrievalService(
                    RetrievalService(session),
                    DenseRetrievalService(
                        session,
                        embedding_provider=embedding_provider,
                        vector_store=vector_store,
                        min_score=min_score,
                    ),
                )
            evaluator = RetrievalEvaluator(retrieval)
            report = await evaluator.evaluate(dataset, top_k=top_k)
    finally:
        if embedding_provider is not None:
            await embedding_provider.aclose()
        if vector_store is not None:
            await vector_store.aclose()
        await engine.dispose()

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate retrieval against a fixed gold-evidence dataset."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"Evaluation dataset path. Default: {DEFAULT_DATASET}",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Maximum retrieved evidence per question. Default: 5",
    )
    parser.add_argument(
        "--strategy",
        choices=("lexical", "dense", "hybrid", "expanded"),
        default="lexical",
        help="Retrieval strategy to evaluate. Default: lexical",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help=(
            "Dense 余弦相似度下限，低于该值的候选在检索层丢弃。"
            "只对 dense 和 hybrid 生效，默认 0 表示不过滤"
        ),
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path for the full JSON report.",
    )
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be at least 1")
    if not 0.0 <= args.min_score <= 1.0:
        parser.error("--min-score must be between 0 and 1")
    report = asyncio.run(
        run_evaluation(
            args.dataset,
            args.top_k,
            strategy=args.strategy,
            min_score=args.min_score,
        )
    )
    from app.evaluation.retrieval import format_evaluation_report

    print(f"min_score={args.min_score:g}")
    print(format_evaluation_report(report))
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
        print(f"\njson_report={args.json_output}")


if __name__ == "__main__":
    main()
