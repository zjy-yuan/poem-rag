from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


async def import_corpus(
    input_path: Path,
    *,
    dry_run: bool,
    rebuild_chunks: bool,
) -> object:
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.schemas.corpus_import import CorpusImportDataset
    from app.services.chunk_catalog import ChunkCatalogService
    from app.services.corpus_import import CorpusImportService

    raw = json.loads(await asyncio.to_thread(input_path.read_text, encoding="utf-8"))
    dataset = CorpusImportDataset.model_validate(raw)
    if dry_run:
        return dataset

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            report = await CorpusImportService(session).import_dataset(dataset)
            if rebuild_chunks:
                chunk_service = ChunkCatalogService(session)
                version_ids = [
                    item.version_id
                    for item in report.records
                    if item.status in {"created", "updated"} and item.version_id is not None
                ]
                for version_id in version_ids:
                    await chunk_service.rebuild_version_chunks(version_id)
    finally:
        await engine.dispose()
    return report


def format_report(report: object) -> str:
    from app.schemas.corpus_import import CorpusImportDataset, CorpusImportReport

    if isinstance(report, CorpusImportDataset):
        return (
            f"dry_run dataset_version={report.version} "
            f"source_key={report.source.source_key} records={len(report.records)}"
        )
    if isinstance(report, CorpusImportReport):
        return (
            f"dataset_version={report.dataset_version} source_key={report.source_key} "
            f"total={report.total_records} created={report.created_records} "
            f"updated={report.updated_records} unchanged={report.unchanged_records} "
            f"failed={report.failed_records}"
        )
    return str(report)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a structured, licensed poem corpus dataset."
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to the JSON dataset.",
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
        help="Validate the dataset without writing to the database.",
    )
    parser.add_argument(
        "--rebuild-chunks",
        action="store_true",
        help="Rebuild structural-v1 chunks for created or updated versions.",
    )
    args = parser.parse_args()

    try:
        report = asyncio.run(
            import_corpus(
                args.input,
                dry_run=args.dry_run,
                rebuild_chunks=args.rebuild_chunks,
            )
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.exit(2, f"import failed: {exc}\n")

    print(format_report(report))
    if args.report is not None:
        from app.schemas.corpus_import import CorpusImportReport

        if isinstance(report, CorpusImportReport):
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(report.model_dump_json(indent=2), encoding="utf-8")
            print(f"json_report={args.report}")


if __name__ == "__main__":
    main()
