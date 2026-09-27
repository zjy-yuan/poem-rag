from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.poem import Poem
from app.models.version import PoemVersion


async def lock_publication_target(
    session: AsyncSession,
    *,
    version_id: int,
) -> int:
    """Lock the poem row before writing vectors for one of its versions.

    Indexing and reconciliation use the same row as the publication
    coordinator. The lock is held until the surrounding database transaction
    commits, so the GC cannot classify a just-written point as orphaned while
    its MySQL reference is still being committed.
    """

    poem_id = await session.scalar(
        select(Poem.id)
        .join(PoemVersion, PoemVersion.poem_id == Poem.id)
        .where(PoemVersion.id == version_id)
        .with_for_update()
    )
    if poem_id is None:
        raise AppError(
            status_code=404,
            code=ErrorCode.POEM_VERSION_NOT_FOUND,
            message="诗词版本不存在",
        )
    return int(poem_id)


async def lock_all_publication_targets(session: AsyncSession) -> None:
    """Serialize vector GC against every in-flight publication."""

    await session.execute(select(Poem.id).with_for_update())
