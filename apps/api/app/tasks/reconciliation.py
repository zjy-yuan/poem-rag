from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import ErrorCode
from app.models.index_run import IndexRunStatus
from app.services.index_runs import IndexRunService
from app.services.task_queue import IndexTaskQueue, create_index_task_queue
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


async def reconcile_stale_index_runs(
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    queue: IndexTaskQueue | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    app_settings = settings or get_settings()
    if not app_settings.index_task_queue_enabled:
        return {
            "status": "disabled",
            "scanned": 0,
            "requeued": 0,
            "failed": 0,
            "skipped": 0,
            "errors": 0,
        }

    engine = None
    if session_factory is None:
        from app.db.session import create_database_engine, create_session_factory

        engine = create_database_engine(app_settings)
        session_factory = create_session_factory(engine)

    task_queue = queue or create_index_task_queue(app_settings)
    current_time = now or datetime.now(UTC)
    pending_stale_before = current_time - timedelta(
        seconds=app_settings.index_task_reconcile_stale_seconds
    )
    counts = {
        "status": "completed",
        "scanned": 0,
        "requeued": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
    }

    try:
        async with session_factory() as session:
            candidates = await IndexRunService(session).list_recovery_candidates(
                now=current_time,
                pending_stale_before=pending_stale_before,
                limit=app_settings.index_task_reconcile_batch_size,
            )
            candidate_rows = [
                {
                    "id": run.id,
                    "status": run.status,
                    "attempt_count": run.attempt_count,
                    "max_attempts": run.max_attempts,
                }
                for run in candidates
            ]

        counts["scanned"] = len(candidate_rows)
        for candidate in candidate_rows:
            run_id = candidate["id"]
            status = candidate["status"]

            if status == IndexRunStatus.RUNNING.value:
                async with session_factory() as session:
                    recovered = await IndexRunService(session).recover_expired_lease(
                        run_id,
                        now=current_time,
                    )
                if recovered.status == IndexRunStatus.FAILED.value:
                    counts["failed"] += 1
                    continue
                if recovered.status != IndexRunStatus.PENDING.value:
                    counts["skipped"] += 1
                    continue
            elif (
                status == IndexRunStatus.PENDING.value
                and candidate["attempt_count"] >= candidate["max_attempts"]
            ):
                async with session_factory() as session:
                    await IndexRunService(session).fail(
                        run_id,
                        error_message="索引任务已达到最大尝试次数",
                        error_code=ErrorCode.INDEX_RUN_ATTEMPTS_EXHAUSTED.value,
                    )
                counts["failed"] += 1
                continue

            try:
                celery_task_id = await asyncio.to_thread(task_queue.enqueue, run_id)
            except Exception:
                counts["errors"] += 1
                logger.exception("Failed to reconcile index run %s", run_id)
                continue

            try:
                async with session_factory() as session:
                    await IndexRunService(session).set_celery_task_id(
                        run_id,
                        celery_task_id=celery_task_id,
                    )
            except Exception:
                counts["errors"] += 1
                logger.exception(
                    "Index run %s was enqueued but task id persistence failed",
                    run_id,
                )
                continue
            counts["requeued"] += 1
    finally:
        if engine is not None:
            await engine.dispose()

    if counts["errors"]:
        counts["status"] = "partial_failure"
    return counts


@celery_app.task(
    bind=True,
    name="app.tasks.reconciliation.reconcile_index_runs",
)
def reconcile_index_runs_task(self: Any) -> dict[str, Any]:
    del self
    return asyncio.run(reconcile_stale_index_runs(settings=get_settings()))
