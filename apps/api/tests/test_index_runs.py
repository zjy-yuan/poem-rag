from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


def test_index_run_idempotency_key_reuses_existing_run(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def create_twice() -> tuple[int, int, str | None]:
        async with session_factory() as session:
            service = IndexRunService(session)
            first = await service.create_run(
                versions[0].id,
                idempotency_key="import-20260927-001",
            )
            second = await service.create_run(
                versions[0].id,
                idempotency_key="import-20260927-001",
            )
            return first.id, second.id, second.idempotency_key

    first_id, second_id, idempotency_key = portal.call(create_twice)

    assert first_id == second_id
    assert idempotency_key == "import-20260927-001"


def test_index_run_claim_is_exclusive_and_heartbeat_requires_owner(
    client: TestClient,
) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def claim_and_heartbeat() -> tuple[str, int, str]:
        async with session_factory() as session:
            service = IndexRunService(session)
            run = await service.create_run(versions[0].id)
            claimed = await service.claim(
                run.id,
                worker_id="worker-a",
                lease_seconds=60,
            )
            await service.heartbeat(
                run.id,
                worker_id="worker-a",
                lease_seconds=60,
            )
            return claimed.status, claimed.attempt_count, claimed.lease_owner or ""

    status, attempt_count, lease_owner = portal.call(claim_and_heartbeat)
    assert status == IndexRunStatus.RUNNING.value
    assert attempt_count == 1
    assert lease_owner == "worker-a"

    async def claim_again_and_heartbeat_from_other_worker() -> None:
        async with session_factory() as session:
            service = IndexRunService(session)
            run = await session.scalar(select(PoemIndexRun))
            assert run is not None
            await service.claim(run.id, worker_id="worker-b", lease_seconds=60)

    with pytest.raises(AppError) as claim_exc:
        portal.call(claim_again_and_heartbeat_from_other_worker)
    assert claim_exc.value.code == ErrorCode.INDEX_RUN_INVALID_STATUS

    async def heartbeat_from_other_worker() -> None:
        async with session_factory() as session:
            service = IndexRunService(session)
            run = await session.scalar(select(PoemIndexRun))
            assert run is not None
            await service.heartbeat(
                run.id,
                worker_id="worker-b",
                lease_seconds=60,
            )

    with pytest.raises(AppError) as heartbeat_exc:
        portal.call(heartbeat_from_other_worker)
    assert heartbeat_exc.value.code == ErrorCode.INDEX_RUN_LEASE_LOST


def test_index_run_cancel_prevents_completion(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def cancel_then_complete() -> None:
        async with session_factory() as session:
            service = IndexRunService(session)
            run = await service.create_run(versions[0].id)
            await service.claim(run.id, worker_id="worker-a", lease_seconds=60)
            cancelled = await service.cancel(run.id)
            assert cancelled.status == IndexRunStatus.CANCELLED.value
            assert cancelled.cancel_requested_at is not None
            assert cancelled.lease_expires_at is None
            await service.succeed(run.id)

    with pytest.raises(AppError) as exc_info:
        portal.call(cancel_then_complete)
    assert exc_info.value.code == ErrorCode.INDEX_RUN_INVALID_STATUS


def test_index_run_reclaims_expired_lease(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def claim_expired_and_reclaim() -> tuple[int, str]:
        async with session_factory() as session:
            service = IndexRunService(session)
            run = await service.create_run(versions[0].id)
            await service.claim(run.id, worker_id="worker-a", lease_seconds=60)
            run = await session.get(PoemIndexRun, run.id)
            assert run is not None
            run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

            reclaimed = await service.claim(
                run.id,
                worker_id="worker-b",
                lease_seconds=60,
            )
            return reclaimed.attempt_count, reclaimed.lease_owner or ""

    attempt_count, lease_owner = portal.call(claim_expired_and_reclaim)
    assert attempt_count == 2
    assert lease_owner == "worker-b"


def test_index_run_claim_honors_max_attempts(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def exhaust_attempt() -> None:
        async with session_factory() as session:
            service = IndexRunService(session)
            run = await service.create_run(versions[0].id, max_attempts=1)
            await service.claim(run.id, worker_id="worker-a", lease_seconds=60)
            run = await session.get(PoemIndexRun, run.id)
            assert run is not None
            run.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
            await service.claim(run.id, worker_id="worker-b", lease_seconds=60)

    with pytest.raises(AppError) as exc_info:
        portal.call(exhaust_attempt)
    assert exc_info.value.code == ErrorCode.INDEX_RUN_ATTEMPTS_EXHAUSTED


def test_index_run_stage_write_requires_live_lease(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def write_with_wrong_worker() -> None:
        async with session_factory() as session:
            service = IndexRunService(session)
            run = await service.create_run(versions[0].id)
            await service.claim(run.id, worker_id="worker-a", lease_seconds=60)
            await service.mark_chunks_ready(
                run.id,
                chunk_count=2,
                worker_id="worker-b",
            )

    with pytest.raises(AppError) as exc_info:
        portal.call(write_with_wrong_worker)
    assert exc_info.value.code == ErrorCode.INDEX_RUN_LEASE_LOST


def test_index_run_release_for_retry_returns_to_pending(client: TestClient) -> None:
    poem = _create_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def release_and_reclaim() -> tuple[str, int, str | None, str | None]:
        async with session_factory() as session:
            service = IndexRunService(session)
            run = await service.create_run(versions[0].id)
            await service.claim(run.id, worker_id="worker-a", lease_seconds=60)
            released = await service.release_for_retry(
                run.id,
                worker_id="worker-a",
                error_code=ErrorCode.EMBEDDING_PROVIDER_ERROR.value,
                error_message="temporary provider failure",
            )
            released_status = released.status
            reclaimed = await service.claim(
                run.id,
                worker_id="worker-b",
                lease_seconds=60,
            )
            return (
                released_status,
                reclaimed.attempt_count,
                reclaimed.lease_owner,
                reclaimed.error_code,
            )

    status, attempt_count, lease_owner, error_code = portal.call(release_and_reclaim)
    assert status == IndexRunStatus.PENDING.value
    assert attempt_count == 2
    assert lease_owner == "worker-b"
    assert error_code is None


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
