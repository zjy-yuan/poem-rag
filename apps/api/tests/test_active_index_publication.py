from __future__ import annotations

from typing import Any

from app.models.chunk import ChunkStatus, PoemChunk
from app.models.index_run import IndexRunStage, IndexRunStatus, PoemIndexRun
from app.models.poem import Poem
from app.repositories.chunks import ChunkRepository
from app.services.chunk_catalog import ChunkCatalogService
from app.services.index_runs import IndexRunService
from app.services.indexing import IndexingService
from app.services.retrieval import RetrievalService
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_catalog import _admin_client
from test_indexing import FakeEmbeddingProvider, FakeVectorStore
from test_rag_corpus import _load_versions


def _published_poem(
    client: TestClient,
    *,
    content: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    headers, _ = _admin_client(client)
    poem = client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={"title": "Moon Poem", "content": content},
    ).json()["data"]
    response = client.post(
        f"/api/v1/admin/poems/{poem['id']}/publish",
        headers=headers,
    )
    assert response.status_code == 200
    return headers, poem


def _index_version(
    client: TestClient,
    version_id: int,
    *,
    rebuild_chunks: bool = True,
) -> int:
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def index() -> int:
        async with session_factory() as session:
            result = await IndexingService(
                session,
                embedding_provider=FakeEmbeddingProvider(),
                vector_store=FakeVectorStore(),
            ).index_version(version_id, rebuild_chunks=rebuild_chunks)
            return result.run_id

    return portal.call(index)


def _lexical_chunk_ids(client: TestClient, query: str) -> list[int]:
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def search() -> list[int]:
        async with session_factory() as session:
            result = await RetrievalService(session).search_evidence(
                query=query,
                limit=10,
            )
            return [item.chunk_id for item in result.items]

    return portal.call(search)


def test_new_version_chunks_stay_invisible_until_their_run_is_published(
    client: TestClient,
) -> None:
    headers, poem = _published_poem(client, content="Moonlight before my bed.")
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    versions = portal.call(_load_versions, session_factory, poem["id"])
    first_run_id = _index_version(client, versions[0].id)
    assert _lexical_chunk_ids(client, "moonlight")

    update = client.patch(
        f"/api/v1/admin/poems/{poem['id']}",
        headers=headers,
        json={"content": "Moonlight over the river.", "version_no": 1},
    )
    assert update.status_code == 200

    versions = portal.call(_load_versions, session_factory, poem["id"])
    assert len(versions) == 2

    async def rebuild() -> list[int]:
        async with session_factory() as session:
            chunks = await ChunkCatalogService(session).rebuild_version_chunks(
                versions[1].id
            )
            return [chunk.id for chunk in chunks]

    new_chunk_ids = portal.call(rebuild)
    assert new_chunk_ids

    # The newer version is already the poem's active version and its chunks
    # exist, but nothing was published for it yet.
    assert _lexical_chunk_ids(client, "moonlight") == []

    async def load_pointer() -> int | None:
        async with session_factory() as session:
            poem_row = await session.get(Poem, poem["id"])
            assert poem_row is not None
            return poem_row.active_index_run_id

    assert portal.call(load_pointer) == first_run_id

    second_run_id = _index_version(client, versions[1].id, rebuild_chunks=False)

    assert portal.call(load_pointer) == second_run_id
    assert set(_lexical_chunk_ids(client, "moonlight")) == set(new_chunk_ids)


def test_run_finishing_after_a_newer_version_does_not_move_the_pointer(
    client: TestClient,
) -> None:
    headers, poem = _published_poem(client, content="Moonlight before my bed.")
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def create_run() -> int:
        async with session_factory() as session:
            run = await IndexRunService(session).create_run(
                versions[0].id,
                config_snapshot={"rebuild_chunks": True},
            )
            return run.id

    run_id = portal.call(create_run)

    update = client.patch(
        f"/api/v1/admin/poems/{poem['id']}",
        headers=headers,
        json={"content": "Moonlight over the river.", "version_no": 1},
    )
    assert update.status_code == 200

    async def execute() -> None:
        async with session_factory() as session:
            await IndexRunService(session).claim(
                run_id,
                worker_id="worker-1",
                lease_seconds=60,
            )
        async with session_factory() as session:
            await IndexingService(
                session,
                embedding_provider=FakeEmbeddingProvider(),
                vector_store=FakeVectorStore(),
            ).index_claimed_run(
                run_id,
                worker_id="worker-1",
                heartbeat_seconds=10,
                lease_seconds=60,
            )

    portal.call(execute)

    async def load_state() -> tuple[PoemIndexRun, Poem, list[PoemChunk]]:
        async with session_factory() as session:
            run = await session.get(PoemIndexRun, run_id)
            poem_row = await session.get(Poem, poem["id"])
            assert run is not None and poem_row is not None
            chunks = list(
                (
                    await session.execute(
                        select(PoemChunk).where(
                            PoemChunk.poem_version_id == versions[0].id
                        )
                    )
                )
                .scalars()
                .all()
            )
            return run, poem_row, chunks

    run, poem_row, chunks = portal.call(load_state)

    assert run.status == IndexRunStatus.SUCCEEDED.value
    assert chunks
    assert all(chunk.index_run_id == run_id for chunk in chunks)
    assert poem_row.active_index_run_id is None
    assert poem_row.version_no == 2
    # The superseded version never becomes visible.
    assert _lexical_chunk_ids(client, "moonlight") == []


def test_poems_without_a_published_run_keep_the_legacy_visibility(
    client: TestClient,
) -> None:
    _, poem = _published_poem(client, content="Moonlight before my bed.")
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def seed_legacy_chunks() -> list[int]:
        async with session_factory() as session:
            chunks = await ChunkCatalogService(session).rebuild_version_chunks(
                versions[0].id
            )
            for index, chunk in enumerate(chunks):
                chunk.vector_id = f"legacy-vector-{index}"
                chunk.embedding_model = "fake-embedding"
                chunk.embedding_dimension = 3
                chunk.status = ChunkStatus.READY.value
            chunk_ids = [chunk.id for chunk in chunks]
            await session.commit()
            return chunk_ids

    chunk_ids = portal.call(seed_legacy_chunks)

    assert chunk_ids
    assert set(_lexical_chunk_ids(client, "moonlight")) == set(chunk_ids)

    async def load_by_vector_ids() -> list[int]:
        async with session_factory() as session:
            candidates = await ChunkRepository(session).list_public_by_vector_ids(
                vector_ids=[f"legacy-vector-{index}" for index in range(len(chunk_ids))]
            )
            return [candidate.chunk_id for candidate in candidates]

    assert set(portal.call(load_by_vector_ids)) == set(chunk_ids)


def test_visibility_hides_chunks_of_a_run_that_is_not_active(
    client: TestClient,
) -> None:
    _, poem = _published_poem(
        client,
        content="Moonlight before my bed.\nMoonlight on the ground.",
    )
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def seed() -> tuple[list[int], list[int]]:
        async with session_factory() as session:
            active_run = PoemIndexRun(
                poem_version_id=versions[0].id,
                status=IndexRunStatus.SUCCEEDED.value,
                stage=IndexRunStage.UPSERT.value,
                chunk_strategy="structural-v1",
                config_snapshot={},
            )
            stale_run = PoemIndexRun(
                poem_version_id=versions[0].id,
                status=IndexRunStatus.FAILED.value,
                stage=IndexRunStage.UPSERT.value,
                chunk_strategy="structural-v1",
                config_snapshot={},
            )
            session.add_all([active_run, stale_run])
            await session.flush()
            chunks = await ChunkCatalogService(session).rebuild_version_chunks(
                versions[0].id
            )
            active_ids: list[int] = []
            stale_ids: list[int] = []
            for index, chunk in enumerate(chunks):
                run = active_run if index % 2 == 0 else stale_run
                chunk.index_run_id = run.id
                chunk.vector_id = f"vector-{index}"
                chunk.embedding_model = "fake-embedding"
                chunk.embedding_dimension = 3
                chunk.status = ChunkStatus.READY.value
                (active_ids if index % 2 == 0 else stale_ids).append(chunk.id)
            poem_row = await session.get(Poem, poem["id"])
            assert poem_row is not None
            poem_row.active_index_run_id = active_run.id
            await session.commit()
            return active_ids, stale_ids

    active_ids, stale_ids = portal.call(seed)
    assert active_ids and stale_ids

    async def load_by_vector_ids() -> list[int]:
        async with session_factory() as session:
            candidates = await ChunkRepository(session).list_public_by_vector_ids(
                vector_ids=[
                    f"vector-{index}"
                    for index in range(len(active_ids) + len(stale_ids))
                ]
            )
            return [candidate.chunk_id for candidate in candidates]

    assert set(portal.call(load_by_vector_ids)) == set(active_ids)
    assert set(_lexical_chunk_ids(client, "moonlight")) == set(active_ids)
