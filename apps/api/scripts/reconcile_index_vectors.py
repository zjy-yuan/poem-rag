"""Inspect and optionally delete orphan Qdrant points.

The script is a dry run unless ``--apply`` is passed. Apply locks all poem
publication targets, rechecks ``poem_chunks.vector_id`` after the inspection
snapshot ends, and only then deletes points that are still unreferenced.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def reconcile(*, apply: bool, sample_size: int) -> None:
    from app.ai.providers.qdrant import create_qdrant_vector_store
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.services.index_reconciliation import IndexReconciliationService

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    vector_store = create_qdrant_vector_store(settings)
    try:
        async with session_factory() as session:
            service = IndexReconciliationService(
                session,
                vector_store=vector_store,
            )
            report = await service.inspect()
            if apply:
                report = await service.apply(report)
    finally:
        await vector_store.aclose()
        await engine.dispose()

    mode = "applied" if apply else "dry-run"
    print(
        f"mode={mode} collection={report.collection} "
        f"expected_points={report.expected_points} "
        f"actual_points={report.actual_points} "
        f"live_points={report.live_points} "
        f"orphan_points={report.orphan_points} "
        f"delete_candidates={len(report.delete_candidate_ids)} "
        f"protected_points={len(report.protected_point_ids)} "
        f"unknown_points={len(report.unknown_point_ids)} "
        f"missing_points={len(report.missing_point_ids)} "
        f"deleted_points={report.deleted_points}"
    )
    _print_sample("delete_candidate_sample", report.delete_candidate_ids, sample_size)
    _print_sample("missing_point_sample", report.missing_point_ids, sample_size)
    _print_sample("unknown_point_sample", report.unknown_point_ids, sample_size)
    if not apply:
        print("Rerun with --apply to delete proven orphan points.")


def _print_sample(label: str, values: tuple[str, ...], sample_size: int) -> None:
    if not values:
        return
    sample = ", ".join(values[:sample_size])
    print(f"{label}={sample}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile MySQL vector references with Qdrant points.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Delete proven orphan points after locking publication targets and "
            "rechecking MySQL references; without it the script only reports."
        ),
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=10,
        help="Maximum point IDs printed per category. Default: 10",
    )
    args = parser.parse_args()
    if args.sample_size < 0:
        parser.error("--sample-size cannot be negative")
    asyncio.run(reconcile(apply=args.apply, sample_size=args.sample_size))


if __name__ == "__main__":
    main()
