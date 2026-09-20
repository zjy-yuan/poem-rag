from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


async def rebuild(version_ids: list[int] | None) -> None:
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.models.chunk import PoemChunk
    from app.models.version import PoemVersion
    from app.services.chunk_catalog import ChunkCatalogService
    from sqlalchemy import func, select

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            if version_ids is None:
                result = await session.execute(select(PoemVersion.id).order_by(PoemVersion.id))
                targets = list(result.scalars())
            else:
                targets = version_ids

            service = ChunkCatalogService(session)
            total = 0
            for version_id in targets:
                chunks = await service.rebuild_version_chunks(version_id)
                total += len(chunks)
                print(f"version={version_id} chunks={len(chunks)}")

            stored = await session.scalar(select(func.count(PoemChunk.id)))
            print(
                f"rebuilt_versions={len(targets)} rebuilt_chunks={total} "
                f"stored_chunks={int(stored or 0)}"
            )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild structural-v1 poem chunks.")
    parser.add_argument(
        "--version-id",
        type=int,
        action="append",
        dest="version_ids",
        help="Poem version ID. Repeat to rebuild multiple versions.",
    )
    args = parser.parse_args()
    asyncio.run(rebuild(args.version_ids))


if __name__ == "__main__":
    main()
