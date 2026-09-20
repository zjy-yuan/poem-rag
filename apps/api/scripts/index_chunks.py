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
    from app.services.indexing import IndexingResult
    from sqlalchemy.ext.asyncio import AsyncSession


async def _resolve_version_ids(
    session: AsyncSession,
    explicit_version_ids: list[int],
    *,
    all_pending: bool,
) -> list[int]:
    if not all_pending:
        return explicit_version_ids

    from app.models.chunk import ChunkStatus, PoemChunk
    from sqlalchemy import select

    result = await session.execute(
        select(PoemChunk.poem_version_id)
        .where(PoemChunk.status == ChunkStatus.PENDING.value)
        .distinct()
        .order_by(PoemChunk.poem_version_id)
    )
    return list(result.scalars())


async def index_chunks(
    version_ids: list[int],
    *,
    all_pending: bool,
    rebuild_chunks: bool,
    chunk_strategy: str,
) -> list[IndexingResult]:
    from app.ai.providers.qdrant import create_qdrant_vector_store
    from app.ai.providers.qwen_embedding import create_qwen_embedding_provider
    from app.core.config import get_settings
    from app.db.session import create_database_engine, create_session_factory
    from app.services.indexing import IndexingService

    settings = get_settings()
    engine = create_database_engine(settings)
    session_factory = create_session_factory(engine)
    embedding_provider = None
    vector_store = None
    results: list[IndexingResult] = []

    try:
        embedding_provider = create_qwen_embedding_provider(settings)
        vector_store = create_qdrant_vector_store(settings)
        async with session_factory() as session:
            targets = await _resolve_version_ids(
                session,
                version_ids,
                all_pending=all_pending,
            )
            if not targets:
                print("No pending poem versions found.")
                return results

            service = IndexingService(
                session,
                embedding_provider=embedding_provider,
                vector_store=vector_store,
            )
            for version_id in targets:
                result = await service.index_version(
                    version_id,
                    rebuild_chunks=rebuild_chunks,
                    chunk_strategy=chunk_strategy,
                )
                results.append(result)
                print(
                    f"version={version_id} run_id={result.run_id} "
                    f"chunks={result.chunk_count} embedded={result.embedded_count} "
                    f"dimension={result.embedding_dimension} "
                    f"collection={result.vector_collection}"
                )
    finally:
        if embedding_provider is not None:
            await embedding_provider.aclose()
        if vector_store is not None:
            await vector_store.aclose()
        await engine.dispose()

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Embed persisted poem chunks with Qwen and upsert them to Qdrant."
    )
    parser.add_argument(
        "--version-id",
        type=int,
        action="append",
        dest="version_ids",
        default=[],
        help="Poem version ID. Repeat to index multiple versions.",
    )
    parser.add_argument(
        "--all-pending",
        action="store_true",
        help="Index every poem version that currently has pending chunks.",
    )
    parser.add_argument(
        "--rebuild-chunks",
        action="store_true",
        help=(
            "Rebuild structural chunks before embedding. "
            "Existing vector IDs will still be rejected."
        ),
    )
    parser.add_argument(
        "--chunk-strategy",
        default="structural-v1",
        help="Chunk strategy to index. Default: structural-v1",
    )
    args = parser.parse_args()

    if args.all_pending and args.version_ids:
        parser.error("--all-pending cannot be combined with --version-id")
    if not args.all_pending and not args.version_ids:
        parser.error("provide --version-id or --all-pending")

    version_ids = list(dict.fromkeys(args.version_ids))
    try:
        results = asyncio.run(
            index_chunks(
                version_ids,
                all_pending=args.all_pending,
                rebuild_chunks=args.rebuild_chunks,
                chunk_strategy=args.chunk_strategy,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"indexing failed: {exc}\n")
    except Exception as exc:
        from app.core.errors import AppError

        if not isinstance(exc, AppError):
            raise
        parser.exit(2, f"indexing failed: {exc.code}: {exc.message}\n")

    print(
        f"indexed_versions={len(results)} "
        f"indexed_chunks={sum(result.chunk_count for result in results)}"
    )


if __name__ == "__main__":
    main()
