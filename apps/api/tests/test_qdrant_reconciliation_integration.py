from __future__ import annotations

import os
import uuid

import pytest
from app.ai.providers.qdrant import QdrantVectorStore
from app.ai.providers.vector_store import VectorPoint
from app.core.config import Settings
from app.models.index_run import IndexRunStage, IndexRunStatus, PoemIndexRun
from app.models.poem import Poem
from app.services.chunk_catalog import ChunkCatalogService
from app.services.index_reconciliation import IndexReconciliationService
from fastapi.testclient import TestClient
from qdrant_client import AsyncQdrantClient
from test_catalog import _admin_client
from test_rag_corpus import _load_versions

_LIVE_POINT = "00000000-0000-0000-0000-000000000101"
_STALE_POINT = "00000000-0000-0000-0000-000000000102"
_PROTECTED_POINT = "00000000-0000-0000-0000-000000000103"
_UNKNOWN_POINT = "00000000-0000-0000-0000-000000000104"

pytestmark = pytest.mark.skipif(
    os.getenv("POEM_QDRANT_INTEGRATION") != "1",
    reason="set POEM_QDRANT_INTEGRATION=1 to run the real Qdrant test",
)


def _qdrant_settings() -> Settings:
    return Settings()


def _require_real_qdrant() -> Settings:
    settings = _qdrant_settings()
    if not settings.qdrant_url:
        pytest.skip("QDRANT_URL is not configured")
    return settings


def _create_published_poem(client: TestClient) -> dict[str, object]:
    headers, _ = _admin_client(client)
    poem = client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={
            "title": "Isolated Reconciliation Poem",
            "content": "First line.\nSecond line.\nThird line.",
        },
    ).json()["data"]
    response = client.post(
        f"/api/v1/admin/poems/{poem['id']}/publish",
        headers=headers,
    )
    assert response.status_code == 200
    return poem


def test_reconciliation_apply_deletes_only_stale_points_in_real_qdrant(
    client: TestClient,
) -> None:
    settings = _require_real_qdrant()
    collection = f"poem_chunks_reconcile_smoke_{uuid.uuid4().hex[:12]}"
    client_store = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=(
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else None
        ),
        timeout=10,
        check_compatibility=False,
    )
    store = QdrantVectorStore(
        url=settings.qdrant_url,
        collection=collection,
        api_key=(
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else None
        ),
        timeout_seconds=10,
        client=client_store,
    )
    portal = client.portal
    assert portal is not None

    try:
        poem = _create_published_poem(client)
        session_factory = client.app.state.session_factory
        versions = portal.call(_load_versions, session_factory, poem["id"])

        async def seed() -> tuple[int, int, int]:
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
                chunks[0].vector_id = _LIVE_POINT
                chunks[0].index_run_id = succeeded.id
                chunks[1].vector_id = "00000000-0000-0000-0000-000000000199"
                chunks[1].index_run_id = succeeded.id
                poem_row = await session.get(Poem, poem["id"])
                assert poem_row is not None
                poem_row.active_index_run_id = succeeded.id
                await session.commit()
                return succeeded.id, running.id, chunks[0].id

        succeeded_run_id, running_run_id, live_chunk_id = portal.call(seed)

        async def seed_qdrant() -> None:
            await store.ensure_collection(dimension=8)
            await store.upsert(
                [
                    VectorPoint(
                        id=_LIVE_POINT,
                        vector=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                        payload={"chunk_id": live_chunk_id},
                    ),
                    VectorPoint(
                        id=_STALE_POINT,
                        vector=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                        payload={"index_run_id": succeeded_run_id},
                    ),
                    VectorPoint(
                        id=_PROTECTED_POINT,
                        vector=[0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                        payload={"index_run_id": running_run_id},
                    ),
                    VectorPoint(
                        id=_UNKNOWN_POINT,
                        vector=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
                        payload={},
                    ),
                ]
            )

        portal.call(seed_qdrant)

        async def inspect_and_apply() -> object:
            async with session_factory() as session:
                service = IndexReconciliationService(
                    session,
                    vector_store=store,
                )
                report = await service.inspect()
                assert report.delete_candidate_ids == (_STALE_POINT,)
                assert report.protected_point_ids == (_PROTECTED_POINT,)
                assert report.unknown_point_ids == (_UNKNOWN_POINT,)
                assert report.missing_point_ids == (
                    "00000000-0000-0000-0000-000000000199",
                )
                return await service.apply(report)

        report = portal.call(inspect_and_apply)
        assert report.deleted_points == 1
        assert report.delete_candidate_ids == (_STALE_POINT,)

        async def remaining_ids() -> list[str]:
            return sorted(point.id for point in await store.list_points())

        remaining = portal.call(remaining_ids)
        assert remaining == sorted(
            [_LIVE_POINT, _PROTECTED_POINT, _UNKNOWN_POINT]
        )
    finally:
        async def cleanup() -> None:
            try:
                if await client_store.collection_exists(collection):
                    await client_store.delete_collection(collection)
            finally:
                await client_store.close()

        portal.call(cleanup)
