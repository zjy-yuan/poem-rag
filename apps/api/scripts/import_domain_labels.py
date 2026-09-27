from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


async def import_domain_labels(
    input_path: Path,
    *,
    dry_run: bool,
) -> object:
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.schemas.domain_label_import import DomainLabelImportDataset
    from app.services.domain_label_import import DomainLabelImportService

    raw = json.loads(await asyncio.to_thread(input_path.read_text, encoding="utf-8"))
    dataset = DomainLabelImportDataset.model_validate(raw)

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            return await DomainLabelImportService(session).import_dataset(
                dataset,
                dry_run=dry_run,
            )
    finally:
        await engine.dispose()


def format_report(report: object) -> str:
    from app.schemas.domain_label_import import DomainLabelImportReport

    if isinstance(report, DomainLabelImportReport):
        return (
            f"dry_run={report.dry_run} dataset_version={report.dataset_version} "
            f"source_key={report.source_key} poem_source_key={report.poem_source_key} "
            f"total={report.total_records} created={report.created_records} "
            f"unchanged={report.unchanged_records} failed={report.failed_records} "
            f"created_labels={report.created_assignments} "
            f"unchanged_labels={report.unchanged_assignments} "
            f"failed_labels={report.failed_assignments}"
        )
    return str(report)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import version-scoped domain labels from a reviewed or public dataset. "
            "All new assignments enter pending review."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to the JSON label dataset.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional path for the full JSON import report.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate and preflight against the database without writing "
            "assignments or changing review state."
        ),
    )
    args = parser.parse_args()

    try:
        report = asyncio.run(
            import_domain_labels(
                args.input,
                dry_run=args.dry_run,
            )
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.exit(2, f"import failed: {exc}\n")

    print(format_report(report))
    if args.report is not None:
        from app.schemas.domain_label_import import DomainLabelImportReport

        if isinstance(report, DomainLabelImportReport):
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report.model_dump_json(indent=2), encoding="utf-8")
            print(f"json_report={args.report}")


if __name__ == "__main__":
    main()
