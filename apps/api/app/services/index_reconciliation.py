from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.vector_store import VectorStoreInventoryPort
from app.models.chunk import PoemChunk
from app.models.index_run import IndexRunStatus, PoemIndexRun
from app.services.index_publication import lock_all_publication_targets

_ACTIVE_RUN_STATUSES = {
    IndexRunStatus.PENDING.value,
    IndexRunStatus.RUNNING.value,
}


@dataclass(frozen=True, slots=True)
class IndexReconciliationReport:
    collection: str
    expected_points: int
    actual_points: int
    live_points: int
    delete_candidate_ids: tuple[str, ...]
    protected_point_ids: tuple[str, ...]
    unknown_point_ids: tuple[str, ...]
    missing_point_ids: tuple[str, ...]
    deleted_points: int = 0

    @property
    def orphan_points(self) -> int:
        return (
            len(self.delete_candidate_ids)
            + len(self.protected_point_ids)
            + len(self.unknown_point_ids)
        )


@dataclass(frozen=True, slots=True)
class _ChunkReference:
    id: int
    vector_id: str | None
    poem_version_id: int
    index_run_id: int | None


class IndexReconciliationService:
    """Compare MySQL vector references with the Qdrant inventory.

    Inspect reads a database snapshot before scanning Qdrant so active runs
    protect in-flight writes. Apply then locks all poem publication targets and
    re-reads vector references before deleting candidates. Missing expected
    points are reported but never repaired here.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        vector_store: VectorStoreInventoryPort,
    ) -> None:
        self.session = session
        self.vector_store = vector_store

    async def inspect(self) -> IndexReconciliationReport:
        chunks = await self._load_chunks()
        chunks_by_id = {
            chunk.id: chunk
            for chunk in chunks
        }
        expected_by_id = {
            chunk.vector_id: chunk
            for chunk in chunks
            if chunk.vector_id is not None
        }
        points = await self.vector_store.list_points()
        run_statuses: dict[int, str | None] = {}
        active_versions: dict[int, bool] = {}

        live_ids: list[str] = []
        delete_candidate_ids: list[str] = []
        protected_point_ids: list[str] = []
        unknown_point_ids: list[str] = []

        for point in points:
            if point.id in expected_by_id:
                live_ids.append(point.id)
                continue

            chunk = chunks_by_id.get(_int_payload(point.payload.get("chunk_id")))
            if chunk is not None and chunk.vector_id == point.id:
                live_ids.append(point.id)
                continue

            run_id = _int_payload(point.payload.get("index_run_id"))
            if run_id is None and chunk is not None:
                run_id = chunk.index_run_id
            if run_id is not None:
                status = await self._run_status(run_id, run_statuses)
                if status in _ACTIVE_RUN_STATUSES:
                    protected_point_ids.append(point.id)
                else:
                    delete_candidate_ids.append(point.id)
                continue

            version_id = _int_payload(point.payload.get("poem_version_id"))
            if version_id is None and chunk is not None:
                version_id = chunk.poem_version_id
            if version_id is not None:
                has_active_run = await self._has_active_run(
                    version_id,
                    active_versions,
                )
                if has_active_run:
                    protected_point_ids.append(point.id)
                else:
                    delete_candidate_ids.append(point.id)
                continue

            unknown_point_ids.append(point.id)

        missing_point_ids = sorted(set(expected_by_id) - {point.id for point in points})
        return IndexReconciliationReport(
            collection=self.vector_store.collection,
            expected_points=len(expected_by_id),
            actual_points=len(points),
            live_points=len(live_ids),
            delete_candidate_ids=tuple(sorted(delete_candidate_ids)),
            protected_point_ids=tuple(sorted(protected_point_ids)),
            unknown_point_ids=tuple(sorted(unknown_point_ids)),
            missing_point_ids=tuple(missing_point_ids),
        )

    async def apply(
        self,
        report: IndexReconciliationReport | None = None,
    ) -> IndexReconciliationReport:
        current = report or await self.inspect()
        if not current.delete_candidate_ids:
            return replace(current, deleted_points=0)

        # End the inspection snapshot before re-reading. MySQL REPEATABLE READ
        # would otherwise keep returning the same pre-publication view.
        await self.session.rollback()
        delete_candidate_ids = current.delete_candidate_ids
        try:
            await lock_all_publication_targets(self.session)
            referenced_ids = await self._load_referenced_vector_ids(
                delete_candidate_ids
            )
            delete_candidate_ids = tuple(
                point_id
                for point_id in delete_candidate_ids
                if point_id not in referenced_ids
            )
            if delete_candidate_ids:
                await self.vector_store.delete(list(delete_candidate_ids))
        finally:
            await self.session.rollback()

        newly_live = len(current.delete_candidate_ids) - len(delete_candidate_ids)
        return replace(
            current,
            live_points=current.live_points + newly_live,
            delete_candidate_ids=delete_candidate_ids,
            deleted_points=len(delete_candidate_ids),
        )

    async def _load_chunks(self) -> list[_ChunkReference]:
        rows = (
            await self.session.execute(
                select(
                    PoemChunk.id,
                    PoemChunk.vector_id,
                    PoemChunk.poem_version_id,
                    PoemChunk.index_run_id,
                )
            )
        ).all()
        return [
            _ChunkReference(
                id=int(row[0]),
                vector_id=row[1],
                poem_version_id=int(row[2]),
                index_run_id=row[3],
            )
            for row in rows
        ]

    async def _load_referenced_vector_ids(
        self,
        vector_ids: tuple[str, ...],
    ) -> set[str]:
        if not vector_ids:
            return set()
        rows = await self.session.execute(
            select(PoemChunk.vector_id).where(
                PoemChunk.vector_id.in_(vector_ids)
            )
        )
        return {
            str(vector_id)
            for (vector_id,) in rows
            if vector_id is not None
        }

    async def _run_status(
        self,
        run_id: int,
        cache: dict[int, str | None],
    ) -> str | None:
        if run_id not in cache:
            cache[run_id] = await self.session.scalar(
                select(PoemIndexRun.status).where(PoemIndexRun.id == run_id)
            )
        return cache[run_id]

    async def _has_active_run(
        self,
        version_id: int,
        cache: dict[int, bool],
    ) -> bool:
        if version_id not in cache:
            active_run_id = await self.session.scalar(
                select(PoemIndexRun.id)
                .where(
                    PoemIndexRun.poem_version_id == version_id,
                    PoemIndexRun.status.in_(_ACTIVE_RUN_STATUSES),
                )
                .limit(1)
            )
            cache[version_id] = active_run_id is not None
        return cache[version_id]


def _int_payload(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
