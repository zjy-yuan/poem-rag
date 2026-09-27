from __future__ import annotations

import asyncio
import os
import socket
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.providers.embedding import EmbeddingProvider
from app.ai.providers.qdrant import VectorStoreError
from app.ai.providers.qwen_embedding import EmbeddingProviderError
from app.ai.providers.vector_store import VectorStorePort
from app.core.config import Settings, get_settings
from app.core.errors import AppError, ErrorCode
from app.models.index_run import IndexRunStatus
from app.services.index_runs import IndexRunService
from app.services.indexing import IndexingService
from app.tasks.celery_app import celery_app

_SKIPPED_ERROR_CODES = {
    ErrorCode.INDEX_RUN_ATTEMPTS_EXHAUSTED,
    ErrorCode.INDEX_RUN_INVALID_STATUS,
    ErrorCode.INDEX_RUN_LEASE_LOST,
}
_RETRYABLE_ERROR_CODES = {
    ErrorCode.EMBEDDING_PROVIDER_ERROR,
    ErrorCode.VECTOR_STORE_ERROR,
    ErrorCode.INTERNAL_ERROR,
}


class IndexTaskRetryRequested(RuntimeError):
    def __init__(self, *, run_id: int, error_code: str) -> None:
        super().__init__(f"index run {run_id} requested retry: {error_code}")
        self.run_id = run_id
        self.error_code = error_code


async def execute_index_run(
    run_id: int,
    *,
    worker_id: str,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_store: VectorStorePort | None = None,
) -> dict[str, Any]:
    app_settings = settings or get_settings()
    engine = None
    if session_factory is None:
        from app.db.session import create_database_engine, create_session_factory

        engine = create_database_engine(app_settings)
        session_factory = create_session_factory(engine)

    owns_embedding_provider = embedding_provider is None
    owns_vector_store = vector_store is None
    if embedding_provider is None:
        from app.ai.providers.qwen_embedding import create_qwen_embedding_provider

        embedding_provider = create_qwen_embedding_provider(app_settings)
    if vector_store is None:
        from app.ai.providers.qdrant import create_qdrant_vector_store

        vector_store = create_qdrant_vector_store(app_settings)

    try:
        async with session_factory() as session:
            runs = IndexRunService(session)
            try:
                run = await runs.claim(
                    run_id,
                    worker_id=worker_id,
                    lease_seconds=app_settings.index_task_lease_seconds,
                )
            except AppError as exc:
                if exc.code in _SKIPPED_ERROR_CODES:
                    return {
                        "run_id": run_id,
                        "status": "skipped",
                        "error_code": exc.code,
                    }
                raise

        async with session_factory() as session:
            service = IndexingService(
                session,
                embedding_provider=embedding_provider,
                vector_store=vector_store,
                session_factory=session_factory,
            )
            try:
                result = await service.index_claimed_run(
                    run.id,
                    worker_id=worker_id,
                    heartbeat_seconds=app_settings.index_task_heartbeat_seconds,
                    lease_seconds=app_settings.index_task_lease_seconds,
                )
            except Exception as exc:
                await session.rollback()
                outcome = await _handle_failure(
                    run_id=run.id,
                    worker_id=worker_id,
                    exc=exc,
                    session_factory=session_factory,
                )
                if outcome == "retry":
                    raise IndexTaskRetryRequested(
                        run_id=run.id,
                        error_code=_error_code(exc),
                    ) from exc
                if outcome == "ignored":
                    return {
                        "run_id": run.id,
                        "status": "skipped",
                        "error_code": _error_code(exc),
                    }
                raise

        return {
            "run_id": result.run_id,
            "status": IndexRunStatus.SUCCEEDED.value,
            "chunk_count": result.chunk_count,
            "embedded_count": result.embedded_count,
            "embedding_dimension": result.embedding_dimension,
            "vector_collection": result.vector_collection,
        }
    finally:
        if owns_embedding_provider and embedding_provider is not None:
            await embedding_provider.aclose()
        if owns_vector_store and vector_store is not None:
            await vector_store.aclose()
        if engine is not None:
            await engine.dispose()


async def _handle_failure(
    *,
    run_id: int,
    worker_id: str,
    exc: Exception,
    session_factory: async_sessionmaker[AsyncSession],
) -> str:
    async with session_factory() as session:
        runs = IndexRunService(session)
        try:
            run = await runs.get_run(run_id)
        except AppError as load_exc:
            if load_exc.code == ErrorCode.INDEX_RUN_NOT_FOUND:
                return "ignored"
            raise

        if run.status == IndexRunStatus.CANCELLED.value:
            return "ignored"

        error_code = _error_code(exc)
        if _is_retryable(exc) and run.attempt_count < run.max_attempts:
            try:
                await runs.release_for_retry(
                    run_id,
                    worker_id=worker_id,
                    error_code=error_code,
                    error_message=str(exc),
                )
            except AppError as retry_exc:
                if retry_exc.code in {
                    ErrorCode.INDEX_RUN_ATTEMPTS_EXHAUSTED,
                    ErrorCode.INDEX_RUN_LEASE_LOST,
                }:
                    return "ignored"
                raise
            return "retry"

        try:
            await runs.fail(
                run_id,
                error_message=str(exc),
                error_code=error_code,
                worker_id=worker_id,
            )
        except AppError as fail_exc:
            if fail_exc.code in {
                ErrorCode.INDEX_RUN_INVALID_STATUS,
                ErrorCode.INDEX_RUN_LEASE_LOST,
            }:
                return "ignored"
            raise
        return "failed"


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, AppError):
        return exc.code in _RETRYABLE_ERROR_CODES
    return isinstance(exc, (EmbeddingProviderError, VectorStoreError))


def _error_code(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return str(exc.code)
    if isinstance(exc, EmbeddingProviderError):
        return ErrorCode.EMBEDDING_PROVIDER_ERROR.value
    if isinstance(exc, VectorStoreError):
        return ErrorCode.VECTOR_STORE_ERROR.value
    return ErrorCode.INTERNAL_ERROR.value


@celery_app.task(
    bind=True,
    name="app.tasks.indexing.index_poem_version",
    acks_late=True,
)
def index_poem_version(self: Any, run_id: int) -> dict[str, Any]:
    settings = get_settings()
    task_id = self.request.id or "inline"
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{task_id}"
    try:
        return asyncio.run(
            execute_index_run(
                run_id,
                worker_id=worker_id,
                settings=settings,
            )
        )
    except IndexTaskRetryRequested as exc:
        raise self.retry(
            exc=exc,
            countdown=settings.index_task_retry_backoff_seconds,
            max_retries=None,
        ) from exc
