from __future__ import annotations

from typing import Any

from app.ai.providers.vector_store import VectorPointSnapshot
from app.models.chunk import PoemChunk
from app.models.index_run import IndexRunStage, IndexRunStatus, PoemIndexRun
from app.models.poem import Poem
from app.services.chunk_catalog import ChunkCatalogService
from app.services.index_reconciliation import IndexReconciliationService
from fastapi.testclient import TestClient
from test_catalog import _admin_client
from test_rag_corpus import _load_versions


class FakeInventoryStore:
    def __init__(self, points: list[VectorPointSnapshot]) -> None:
        self._collection = "test-poem-chunks"
        self.points = points
        self.deleted: list[list[str]] = []

    @property
    def collection(self) -> str:
        return self._collection

    async def list_points(self) -> list[VectorPointSnapshot]:
        return list(self.points)

    async def delete(self, point_ids: list[str]) -> None:
        self.deleted.append(point_ids)


def _create_published_poem(client: TestClient) -> dict[str, Any]:
    headers, _ = _admin_client(client)
    poem = client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={
            "title": "Reconciliation Poem",
            "content": "第一行。\n第二行。\n第三行。",
        },
    ).json()["data"]
    response = client.post(
        f"/api/v1/admin/poems/{poem['id']}/publish",
        headers=headers,
    )
    assert response.status_code == 200
    return poem


def test_reconciliation_reports_live_missing_and_orphan_points(
    client: TestClient,
) -> None:
    poem = _create_published_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def seed() -> tuple[int, int]:
        async with session_factory() as session:
            succeeded = PoemIndexRun(
                poem_version_id=versions[0].id,
                status=IndexRunStatus.SUCCEEDED.value,
                stage=IndexRunStage.UPSERT.value,
                chunk_strategy="structural-v1",
                config_snapshot={},
            )
            running = PoemIndexRun(
                poem_version_id=versions[0].id,
                status=IndexRunStatus.RUNNING.value,
                stage=IndexRunStage.EMBED.value,
                chunk_strategy="structural-v1",
                config_snapshot={},
            )
            session.add_all([succeeded, running])
            await session.flush()
            chunks = await ChunkCatalogService(session).rebuild_version_chunks(
                versions[0].id
            )
            assert len(chunks) >= 2
            chunks[0].vector_id = "live-point"
            chunks[0].index_run_id = succeeded.id
            chunks[1].vector_id = "missing-point"
            chunks[1].index_run_id = succeeded.id
            poem_row = await session.get(Poem, poem["id"])
            assert poem_row is not None
            poem_row.active_index_run_id = succeeded.id
            await session.commit()
            return succeeded.id, running.id

    succeeded_run_id, running_run_id = portal.call(seed)
    store = FakeInventoryStore(
        [
            VectorPointSnapshot(id="live-point", payload={"chunk_id": 1}),
            VectorPointSnapshot(
                id="stale-point",
                payload={"index_run_id": succeeded_run_id},
            ),
            VectorPointSnapshot(
                id="protected-point",
                payload={"index_run_id": running_run_id},
            ),
            VectorPointSnapshot(id="unknown-point", payload={}),
        ]
    )

    async def inspect_and_apply() -> Any:
        async with session_factory() as session:
            service = IndexReconciliationService(session, vector_store=store)
            report = await service.inspect()
            return await service.apply(report)

    report = portal.call(inspect_and_apply)

    assert report.expected_points == 2
    assert report.actual_points == 4
    assert report.live_points == 1
    assert report.delete_candidate_ids == ("stale-point",)
    assert report.protected_point_ids == ("protected-point",)
    assert report.unknown_point_ids == ("unknown-point",)
    assert report.missing_point_ids == ("missing-point",)
    assert report.deleted_points == 1
    assert store.deleted == [["stale-point"]]


def test_reconciliation_protects_legacy_point_during_active_run(
    client: TestClient,
) -> None:
    poem = _create_published_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def seed() -> tuple[int, int]:
        async with session_factory() as session:
            running = PoemIndexRun(
                poem_version_id=versions[0].id,
                status=IndexRunStatus.RUNNING.value,
                stage=IndexRunStage.EMBED.value,
                chunk_strategy="structural-v1",
                config_snapshot={},
            )
            session.add(running)
            await session.flush()
            chunks = await ChunkCatalogService(session).rebuild_version_chunks(
                versions[0].id
            )
            assert chunks
            await session.commit()
            return running.id, chunks[0].id

    _, chunk_id = portal.call(seed)
    store = FakeInventoryStore(
        [
            VectorPointSnapshot(
                id="legacy-point",
                payload={
                    "chunk_id": chunk_id,
                    "poem_version_id": versions[0].id,
                },
            )
        ]
    )

    async def inspect() -> Any:
        async with session_factory() as session:
            return await IndexReconciliationService(
                session,
                vector_store=store,
            ).inspect()

    report = portal.call(inspect)

    assert report.delete_candidate_ids == ()
    assert report.protected_point_ids == ("legacy-point",)
    assert report.unknown_point_ids == ()
    assert store.deleted == []


def test_reconciliation_rechecks_references_before_delete(
    client: TestClient,
) -> None:
    poem = _create_published_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def seed() -> tuple[int, int]:
        async with session_factory() as session:
            succeeded = PoemIndexRun(
                poem_version_id=versions[0].id,
                status=IndexRunStatus.SUCCEEDED.value,
                stage=IndexRunStage.UPSERT.value,
                chunk_strategy="structural-v1",
                config_snapshot={},
            )
            session.add(succeeded)
            await session.flush()
            chunks = await ChunkCatalogService(session).rebuild_version_chunks(
                versions[0].id
            )
            assert len(chunks) >= 2
            chunks[0].vector_id = "already-live"
            chunks[0].index_run_id = succeeded.id
            poem_row = await session.get(Poem, poem["id"])
            assert poem_row is not None
            poem_row.active_index_run_id = succeeded.id
            await session.commit()
            return succeeded.id, chunks[1].id

    succeeded_run_id, late_chunk_id = portal.call(seed)
    store = FakeInventoryStore(
        [
            VectorPointSnapshot(
                id="already-live",
                payload={"chunk_id": 1},
            ),
            VectorPointSnapshot(
                id="late-reference",
                payload={"index_run_id": succeeded_run_id},
            ),
        ]
    )

    async def inspect() -> Any:
        async with session_factory() as session:
            return await IndexReconciliationService(
                session,
                vector_store=store,
            ).inspect()

    report = portal.call(inspect)
    assert report.delete_candidate_ids == ("late-reference",)
    assert report.live_points == 1

    async def commit_late_reference() -> None:
        async with session_factory() as session:
            chunk = await session.get(PoemChunk, late_chunk_id)
            assert chunk is not None
            chunk.vector_id = "late-reference"
            chunk.index_run_id = succeeded_run_id
            await session.commit()

    portal.call(commit_late_reference)

    async def apply() -> Any:
        async with session_factory() as session:
            return await IndexReconciliationService(
                session,
                vector_store=store,
            ).apply(report)

    updated = portal.call(apply)

    assert updated.live_points == 2
    assert updated.delete_candidate_ids == ()
    assert updated.deleted_points == 0
    assert store.deleted == []
