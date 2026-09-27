"""Point every indexed poem at the run that published its active version.

Poems indexed before the publication pointer existed keep the legacy
version-based visibility until this script tags them. It is a dry run unless
``--apply`` is passed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def backfill(*, apply: bool) -> None:
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.models.chunk import PoemChunk
    from app.models.index_run import IndexRunStatus, PoemIndexRun
    from app.models.poem import Poem
    from app.models.version import PoemVersion
    from sqlalchemy import func, select, update

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            total_poems = int(await session.scalar(select(func.count(Poem.id))) or 0)
            without_pointer = int(
                await session.scalar(
                    select(func.count(Poem.id)).where(
                        Poem.active_index_run_id.is_(None)
                    )
                )
                or 0
            )
            rows = (
                await session.execute(
                    select(Poem.id, PoemIndexRun.id)
                    .join(PoemVersion, PoemVersion.poem_id == Poem.id)
                    .join(
                        PoemIndexRun,
                        (PoemIndexRun.poem_version_id == PoemVersion.id)
                        & (PoemVersion.version_no == Poem.version_no)
                        & (PoemIndexRun.status == IndexRunStatus.SUCCEEDED.value),
                    )
                    .where(Poem.active_index_run_id.is_(None))
                    .order_by(Poem.id, PoemIndexRun.id.desc())
                )
            ).all()

            candidates: dict[int, int] = {}
            for poem_id, run_id in rows:
                candidates.setdefault(int(poem_id), int(run_id))

            chunks_to_tag = 0
            tagged_chunks = 0
            publishable_poems = 0
            skipped_ambiguous: list[int] = []
            for poem_id, run_id in candidates.items():
                run = await session.get(PoemIndexRun, run_id)
                if run is None:
                    continue
                conflicting = int(
                    await session.scalar(
                        select(func.count(PoemChunk.id)).where(
                            PoemChunk.poem_version_id == run.poem_version_id,
                            PoemChunk.vector_id.is_not(None),
                            PoemChunk.index_run_id.is_not(None),
                            PoemChunk.index_run_id != run_id,
                        )
                    )
                    or 0
                )
                if conflicting:
                    skipped_ambiguous.append(poem_id)
                    continue
                untagged = int(
                    await session.scalar(
                        select(func.count(PoemChunk.id)).where(
                            PoemChunk.poem_version_id == run.poem_version_id,
                            PoemChunk.vector_id.is_not(None),
                            PoemChunk.index_run_id.is_(None),
                        )
                    )
                    or 0
                )
                if untagged == 0:
                    continue
                chunks_to_tag += untagged
                publishable_poems += 1
                if apply:
                    result = await session.execute(
                        update(PoemChunk)
                        .where(
                            PoemChunk.poem_version_id == run.poem_version_id,
                            PoemChunk.vector_id.is_not(None),
                            PoemChunk.index_run_id.is_(None),
                        )
                        .values(index_run_id=run_id)
                    )
                    tagged_chunks += int(result.rowcount or 0)
                    await session.execute(
                        update(Poem)
                        .where(
                            Poem.id == poem_id,
                            Poem.active_index_run_id.is_(None),
                        )
                        .values(active_index_run_id=run_id)
                    )

            if apply:
                await session.commit()

            mode = "applied" if apply else "dry-run"
            print(
                f"mode={mode} poems_total={total_poems} "
                f"poems_without_pointer={without_pointer} "
                f"publishable_poems={publishable_poems} "
                f"chunks_to_tag={chunks_to_tag} "
                f"tagged_chunks={tagged_chunks} "
                f"skipped_ambiguous={len(skipped_ambiguous)}"
            )
            if skipped_ambiguous:
                preview = ", ".join(str(poem_id) for poem_id in skipped_ambiguous[:20])
                print(f"poems_with_conflicting_chunks={preview}")
            if not apply:
                print("Rerun with --apply to write these changes.")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill poems.active_index_run_id from succeeded index runs.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the pointer and chunk tags; without it the script only reports.",
    )
    args = parser.parse_args()
    asyncio.run(backfill(apply=args.apply))


if __name__ == "__main__":
    main()
