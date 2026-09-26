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

DEFAULT_REPORT = (
    PROJECT_ROOT
    / "data"
    / "eval"
    / "reports"
    / "generation_holdout_1000_v3.json"
)

if TYPE_CHECKING:
    from app.schemas.generation_judge import GenerationJudgeReport


async def run_judge(
    report_path: Path,
    *,
    limit: int | None,
) -> GenerationJudgeReport:
    from app.ai.providers.deepseek import create_deepseek_chat_provider
    from app.core.config import get_settings
    from app.evaluation.generation_judge import GenerationJudge
    from app.schemas.generation_evaluation import GenerationEvaluationReport

    settings = get_settings()
    provider = create_deepseek_chat_provider(settings)
    if provider is None:
        raise RuntimeError(
            "DEEPSEEK_API_KEY 未配置，无法执行生成质量 judge"
        )
    report_content = await asyncio.to_thread(
        report_path.read_text,
        encoding="utf-8",
    )
    source_report = GenerationEvaluationReport.model_validate_json(
        report_content
    )
    try:
        return await GenerationJudge(provider).evaluate(
            source_report,
            limit=limit,
        )
    finally:
        await provider.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Judge an existing generation evaluation report with an independent "
            "LLM call. This command does not rerun retrieval or answer generation."
        )
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"Source generation report. Default: {DEFAULT_REPORT}",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path for the full judge JSON report.",
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        default=None,
        help="Optional path for an anonymized manual-review Markdown sheet.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only judge the first N cases. Useful for a small paid smoke test.",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须大于 0")

    try:
        report = asyncio.run(run_judge(args.report, limit=args.limit))
    except RuntimeError as exc:
        parser.error(str(exc))

    from app.evaluation.generation_judge import (
        export_blind_review_markdown,
        format_generation_judge_report,
    )

    print(format_generation_judge_report(report))
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            report.model_dump_json(indent=2),
            encoding="utf-8",
        )
        print(f"\njson_report={args.json_output}")
    if args.review_output is not None:
        args.review_output.parent.mkdir(parents=True, exist_ok=True)
        args.review_output.write_text(
            export_blind_review_markdown(report),
            encoding="utf-8",
        )
        print(f"\nreview_sheet={args.review_output}")


if __name__ == "__main__":
    main()
