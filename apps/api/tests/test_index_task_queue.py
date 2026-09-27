from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.ai.providers.qwen_embedding import EmbeddingProviderError
from app.core.config import Settings
from app.core.errors import ErrorCode
from app.models.index_run import IndexRunStatus, PoemIndexRun
from app.services.index_runs import IndexRunService
from app.tasks.indexing import IndexTaskRetryRequested, execute_index_run
from app.tasks.reconciliation import reconcile_stale_index_runs
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_catalog import _admin_client
from test_indexing import FakeEmbeddingProvider, FakeVectorStore
from test_rag_corpus import _load_versions


class FakeIndexTaskQueue:
    def __init__(self) -> None:
        self.enqueued_run_ids: list[int] = []

    def enqueue(self, run_id: int) -> str:
        self.enqueued_run_ids.append(run_id)
        return f"fake-task-{len(self.enqueued_run_ids)}"


def _create_admin_poem(client: TestClient) -> tuple[dict[str, str], dict[str, Any]]:
    headers, _ = _admin_client(client)
    poem = client.post(
        "/api/v1/admin/poems",
        headers=headers,
        json={
            "title": "Queued Index Poem",
            "content": "First line.\nSecond line.",
        },
    ).json()["data"]
    return headers, poem


def _create_run_for_worker(
    client: TestClient,
    *,
    max_attempts: int = 3,
) -> int:
    _, poem = _create_admin_poem(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    versions = portal.call(_load_versions, session_factory, poem["id"])

    async def create_run() -> int:
        async with session_factory() as session:
            run = await IndexRunService(session).create_run(
                versions[0].id,
                max_attempts=max_attempts,
            )
            return run.id

    return portal.call(create_run)


def _load_run(client: TestClient, run_id: int) -> PoemIndexRun:
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def load() -> PoemIndexRun:
        async with session_factory() as session:
            run = await session.get(PoemIndexRun, run_id)
            assert run is not None
            return run

    return portal.call(load)


def test_create_index_run_returns_503_when_queue_disabled(
    client: TestClient,
) -> None:
    headers, poem = _create_admin_poem(client)
    portal = client.portal
    assert portal is not None
    versions = portal.call(
        _load_versions,
        client.app.state.session_factory,
        poem["id"],
    )

    response = client.post(
        "/api/v1/admin/index-runs",
        headers={"Authorization": "Bearer invalid"},
        json={"poem_version_id": versions[0].id},
    )

    assert response.status_code == 401

    response = client.post(
        "/api/v1/admin/index-runs",
        headers=headers,
        json={"poem_version_id": versions[0].id},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == ErrorCode.TASK_QUEUE_UNAVAILABLE

    run = portal.call(_load_failed_run, client.app.state.session_factory)
    assert run is not None
    assert run.status == IndexRunStatus.FAILED.value
    assert run.error_code == ErrorCode.TASK_QUEUE_UNAVAILABLE.value


def test_create_index_run_enqueues_once_with_idempotency_key(
    client: TestClient,
) -> None:
    headers, poem = _create_admin_poem(client)
    portal = client.portal
    assert portal is not None
    versions = portal.call(
        _load_versions,
        client.app.state.session_factory,
        poem["id"],
    )
    queue = FakeIndexTaskQueue()
    client.app.state.index_task_queue = queue
    payload = {
        "poem_version_id": versions[0].id,
        "rebuild_chunks": True,
    }
    request_headers = {**headers, "Idempotency-Key": "index-api-20260927-001"}

    first = client.post(
        "/api/v1/admin/index-runs",
        headers=request_headers,
        json=payload,
    )
    second = client.post(
        "/api/v1/admin/index-runs",
        headers=request_headers,
        json=payload,
    )

    assert first.status_code == 202
    assert second.status_code == 202
    first_run = first.json()["data"]
    second_run = second.json()["data"]
    assert first_run["id"] == second_run["id"]
    assert first_run["status"] == IndexRunStatus.PENDING.value
    assert first_run["celery_task_id"] == "fake-task-1"
    assert queue.enqueued_run_ids == [first_run["id"]]


def test_retry_index_run_enqueues_again(client: TestClient) -> None:
    headers, poem = _create_admin_poem(client)
    portal = client.portal
    assert portal is not None
    versions = portal.call(
        _load_versions,
        client.app.state.session_factory,
        poem["id"],
    )
    failed = client.post(
        "/api/v1/admin/index-runs",
        headers=headers,
        json={"poem_version_id": versions[0].id},
    )
    assert failed.status_code == 503
    run = portal.call(_load_failed_run, client.app.state.session_factory)
    assert run is not None

    queue = FakeIndexTaskQueue()
    client.app.state.index_task_queue = queue
    retried = client.post(
        f"/api/v1/admin/index-runs/{run.id}/retry",
        headers=headers,
    )

    assert retried.status_code == 202
    assert retried.json()["data"]["status"] == IndexRunStatus.PENDING.value
    assert retried.json()["data"]["celery_task_id"] == "fake-task-1"
    assert queue.enqueued_run_ids == [run.id]


def test_worker_executes_claimed_run(
    client: TestClient,
    api_settings: Settings,
) -> None:
    run_id = _create_run_for_worker(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    provider = FakeEmbeddingProvider()
    store = FakeVectorStore()

    async def run_worker() -> dict[str, Any]:
        return await execute_index_run(
            run_id,
            worker_id="worker-success",
            settings=api_settings,
            session_factory=session_factory,
            embedding_provider=provider,
            vector_store=store,
        )

    result = portal.call(run_worker)
    run = _load_run(client, run_id)

    assert result["status"] == IndexRunStatus.SUCCEEDED.value
    assert result["chunk_count"] == 3
    assert run.status == IndexRunStatus.SUCCEEDED.value
    assert run.attempt_count == 1
    assert run.lease_owner is None
    assert run.lease_expires_at is None
    assert len(store.upserts) == 1


def test_worker_retries_then_fails_using_database_attempts(
    client: TestClient,
    api_settings: Settings,
) -> None:
    run_id = _create_run_for_worker(client, max_attempts=2)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    provider = FakeEmbeddingProvider(fail=True)
    store = FakeVectorStore()

    async def run_worker() -> dict[str, Any]:
        return await execute_index_run(
            run_id,
            worker_id="worker-retry",
            settings=api_settings,
            session_factory=session_factory,
            embedding_provider=provider,
            vector_store=store,
        )

    with pytest.raises(IndexTaskRetryRequested):
        portal.call(run_worker)
    retryable = _load_run(client, run_id)
    assert retryable.status == IndexRunStatus.PENDING.value
    assert retryable.attempt_count == 1
    assert retryable.error_code == ErrorCode.EMBEDDING_PROVIDER_ERROR.value

    with pytest.raises(EmbeddingProviderError):
        portal.call(run_worker)
    failed = _load_run(client, run_id)
    assert failed.status == IndexRunStatus.FAILED.value
    assert failed.attempt_count == 2
    assert failed.error_code == ErrorCode.EMBEDDING_PROVIDER_ERROR.value


def test_worker_skips_cancelled_run(
    client: TestClient,
    api_settings: Settings,
) -> None:
    run_id = _create_run_for_worker(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory

    async def claim_and_cancel() -> None:
        async with session_factory() as session:
            service = IndexRunService(session)
            await service.claim(
                run_id,
                worker_id="worker-cancelled",
                lease_seconds=60,
            )
            await service.cancel(run_id)

    portal.call(claim_and_cancel)

    async def run_worker() -> dict[str, Any]:
        return await execute_index_run(
            run_id,
            worker_id="worker-after-cancel",
            settings=api_settings,
            session_factory=session_factory,
            embedding_provider=FakeEmbeddingProvider(),
            vector_store=FakeVectorStore(),
        )

    result = portal.call(run_worker)
    run = _load_run(client, run_id)

    assert result == {
        "run_id": run_id,
        "status": "skipped",
        "error_code": ErrorCode.INDEX_RUN_INVALID_STATUS.value,
    }
    assert run.status == IndexRunStatus.CANCELLED.value


def test_reconcile_requeues_stale_pending_run(
    client: TestClient,
    api_settings: Settings,
) -> None:
    run_id = _create_run_for_worker(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    queue = FakeIndexTaskQueue()
    settings = api_settings.model_copy(
        update={
            "index_task_queue_enabled": True,
            "celery_broker_url": "memory://",
        }
    )
    now = datetime.now(UTC)

    async def make_stale() -> None:
        async with session_factory() as session:
            run = await session.get(PoemIndexRun, run_id)
            assert run is not None
            run.updated_at = now - timedelta(seconds=600)
            await session.commit()

    portal.call(make_stale)

    async def reconcile() -> dict[str, Any]:
        return await reconcile_stale_index_runs(
            settings=settings,
            session_factory=session_factory,
            queue=queue,
            now=now,
        )

    result = portal.call(reconcile)
    run = _load_run(client, run_id)

    assert result["status"] == "completed"
    assert result["scanned"] == 1
    assert result["requeued"] == 1
    assert result["failed"] == 0
    assert queue.enqueued_run_ids == [run_id]
    assert run.celery_task_id == "fake-task-1"


def test_reconcile_recovers_expired_lease(
    client: TestClient,
    api_settings: Settings,
) -> None:
    run_id = _create_run_for_worker(client)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    queue = FakeIndexTaskQueue()
    settings = api_settings.model_copy(
        update={
            "index_task_queue_enabled": True,
            "celery_broker_url": "memory://",
        }
    )
    now = datetime.now(UTC)

    async def expire_lease() -> None:
        async with session_factory() as session:
            service = IndexRunService(session)
            await service.claim(run_id, worker_id="worker-lost", lease_seconds=60)
            run = await session.get(PoemIndexRun, run_id)
            assert run is not None
            run.lease_expires_at = now - timedelta(seconds=1)
            await session.commit()

    portal.call(expire_lease)

    async def reconcile() -> dict[str, Any]:
        return await reconcile_stale_index_runs(
            settings=settings,
            session_factory=session_factory,
            queue=queue,
            now=now,
        )

    result = portal.call(reconcile)
    run = _load_run(client, run_id)

    assert result["requeued"] == 1
    assert result["failed"] == 0
    assert queue.enqueued_run_ids == [run_id]
    assert run.status == IndexRunStatus.PENDING.value
    assert run.attempt_count == 1
    assert run.lease_owner is None
    assert run.lease_expires_at is None
    assert run.error_code == ErrorCode.INDEX_RUN_LEASE_EXPIRED.value


def test_reconcile_fails_exhausted_expired_lease(
    client: TestClient,
    api_settings: Settings,
) -> None:
    run_id = _create_run_for_worker(client, max_attempts=1)
    portal = client.portal
    assert portal is not None
    session_factory = client.app.state.session_factory
    queue = FakeIndexTaskQueue()
    settings = api_settings.model_copy(
        update={
            "index_task_queue_enabled": True,
            "celery_broker_url": "memory://",
        }
    )
    now = datetime.now(UTC)

    async def expire_last_attempt() -> None:
        async with session_factory() as session:
            service = IndexRunService(session)
            await service.claim(run_id, worker_id="worker-lost", lease_seconds=60)
            run = await session.get(PoemIndexRun, run_id)
            assert run is not None
            run.lease_expires_at = now - timedelta(seconds=1)
            await session.commit()

    portal.call(expire_last_attempt)

    async def reconcile() -> dict[str, Any]:
        return await reconcile_stale_index_runs(
            settings=settings,
            session_factory=session_factory,
            queue=queue,
            now=now,
        )

    result = portal.call(reconcile)
    run = _load_run(client, run_id)

    assert result["requeued"] == 0
    assert result["failed"] == 1
    assert queue.enqueued_run_ids == []
    assert run.status == IndexRunStatus.FAILED.value
    assert run.error_code == ErrorCode.INDEX_RUN_LEASE_EXPIRED.value


def test_reconcile_is_disabled_without_task_queue(
    client: TestClient,
    api_settings: Settings,
) -> None:
    portal = client.portal
    assert portal is not None
    queue = FakeIndexTaskQueue()

    async def reconcile() -> dict[str, Any]:
        return await reconcile_stale_index_runs(
            settings=api_settings,
            session_factory=client.app.state.session_factory,
            queue=queue,
        )

    result = portal.call(reconcile)

    assert result["status"] == "disabled"
    assert result["scanned"] == 0
    assert queue.enqueued_run_ids == []


async def _load_failed_run(
    session_factory: async_sessionmaker[AsyncSession],
) -> PoemIndexRun | None:
    async with session_factory() as session:
        return await session.scalar(
            select(PoemIndexRun)
            .where(PoemIndexRun.status == IndexRunStatus.FAILED.value)
            .order_by(PoemIndexRun.id.desc())
        )
