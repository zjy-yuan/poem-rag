from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

if TYPE_CHECKING:
    from app.schemas.domain_label_gold import DomainLabelGoldEvaluationReport


async def evaluate_domain_labels(
    input_path: Path,
) -> DomainLabelGoldEvaluationReport:
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.evaluation.domain_label_gold import DomainLabelGoldEvaluator
    from app.schemas.domain_label_gold import DomainLabelGoldDataset

    raw = json.loads(await asyncio.to_thread(input_path.read_text, encoding="utf-8"))
    dataset = DomainLabelGoldDataset.model_validate(raw)
    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            return await DomainLabelGoldEvaluator(session).evaluate(dataset)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate approved online-visible domain labels against a frozen "
            "human gold standard. The command is read-only."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to the frozen domain-label gold dataset.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path for the full JSON evaluation report.",
    )
    args = parser.parse_args()

    try:
        report = asyncio.run(evaluate_domain_labels(args.input))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.exit(2, f"evaluation failed: {exc}\n")

    from app.evaluation.domain_label_gold import format_domain_label_gold_report

    print(format_domain_label_gold_report(report))
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\njson_output={args.json_output}")


if __name__ == "__main__":
    main()
