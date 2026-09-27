from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

if TYPE_CHECKING:
    from app.schemas.domain_label_evaluation import DomainLabelAuditReport


async def run_audit() -> DomainLabelAuditReport:
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.evaluation.domain_labels import DomainLabelAuditor

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            return await DomainLabelAuditor(session).audit()
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Audit domain-label coverage, review backlog and governance risks "
            "on the current published poem corpus."
        )
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path for the full JSON audit report.",
    )
    args = parser.parse_args()

    report = asyncio.run(run_audit())
    from app.evaluation.domain_labels import format_domain_label_audit_report

    print(format_domain_label_audit_report(report))
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            report.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\njson_output={args.json_output}")


if __name__ == "__main__":
    main()
