from __future__ import annotations

from typing import Any

import pytest
from app.core.errors import AppError, ErrorCode
from app.models.index_run import IndexRunStage, IndexRunStatus, PoemIndexRun
from app.models.poem import Poem
from app.services.index_runs import IndexRunService
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_catalog import _admin_client
from test_rag_corpus import _load_versions


def _create_poem(client: TestClient) -> dict[str, Any]:
    headers, _ = _admin_client(client)
    return client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={
            "title": "Indexed Poem",
            "content": "First line.\nSecond line.",
        },
    ).json()["data"]


def test_index_run_lifecycle(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def run_lifecycle() -> tuple[str, str, int, int, str]:
        async with session_factory() as session:
            service = IndexRunService(session)
            run = await service.create_run(
                versions[0].id,
                config_snapshot={"chunker": "structural-v1"},
                embedding_model="test-embedding",
                embedding_dimension=8,
                vector_collection="poem-test",
            )
            await service.start(run.id)
            await service.mark_chunks_ready(run.id, chunk_count=2)
            await service.mark_embeddings_ready(run.id, embedded_count=2)
            finished = await service.succeed(run.id)
            return (
                finished.status,
                finished.stage,
                finished.chunk_count,
                finished.embedded_count,
                finished.vector_collection or "",
            )

    status, stage, chunk_count, embedded_count, collection = portal.call(run_lifecycle)

    assert status == IndexRunStatus.SUCCEEDED.value
    assert stage == IndexRunStage.UPSERT.value
    assert chunk_count == 2
    assert embedded_count == 2
    assert collection == "poem-test"


def test_index_run_sanitizes_secret_config(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def create_run() -> dict[str, Any]:
        async with session_factory() as session:
            run = await IndexRunService(session).create_run(
                versions[0].id,
                config_snapshot={
                    "provider": "qwen",
                    "api_key": "must-not-be-stored",
                    "nested": {
                        "access_token": "must-not-be-stored",
                        "model": "test-model",
                    },
                },
            )
            return run.config_snapshot

    snapshot = portal.call(create_run)

    assert snapshot == {
        "provider": "qwen",
        "api_key": "[REDACTED]",
        "nested": {
            "access_token": "[REDACTED]",
            "model": "test-model",
        },
    }


def test_index_run_rejects_another_active_run(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def create_duplicate_active_run() -> None:
        async with session_factory() as session:
            service = IndexRunService(session)
            await service.create_run(versions[0].id)
            await service.create_run(versions[0].id)

    with pytest.raises(AppError) as exc_info:
        portal.call(create_duplicate_active_run)
    assert exc_info.value.code == ErrorCode.INDEX_RUN_ALREADY_ACTIVE


def test_index_run_rejects_invalid_transition(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def invalid_transition() -> None:
        async with session_factory() as session:
            service = IndexRunService(session)
            run = await service.create_run(versions[0].id)
            await service.mark_chunks_ready(run.id, chunk_count=2)

    with pytest.raises(AppError) as exc_info:
        portal.call(invalid_transition)
    assert exc_info.value.code == ErrorCode.INDEX_RUN_INVALID_STATUS


def test_failed_index_run_records_error(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def fail_run() -> tuple[str, str]:
        async with session_factory() as session:
            service = IndexRunService(session)
            run = await service.create_run(versions[0].id)
            await service.start(run.id)
            failed = await service.fail(run.id, error_message="provider timeout")
            return failed.status, failed.error_message or ""

    status, message = portal.call(fail_run)

    assert status == IndexRunStatus.FAILED.value
    assert message == "provider timeout"


def test_deleting_poem_cascades_index_runs(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def create_and_delete() -> int:
        async with session_factory() as session:
            run = await IndexRunService(session).create_run(versions[0].id)
            run_id = run.id
            stored_poem = await session.get(Poem, poem["id"])
            assert stored_poem is not None
            await session.delete(stored_poem)
            await session.commit()
            remaining = await session.scalar(
                select(PoemIndexRun.id).where(PoemIndexRun.id == run_id)
            )
            return run_id if remaining is None else -1

    assert portal.call(create_and_delete) > 0
