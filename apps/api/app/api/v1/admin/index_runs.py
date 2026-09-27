from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_app_settings,
    get_db_session,
    require_admin,
)
from app.core.config import Settings
from app.core.response import success_response
from app.models.index_run import IndexRunStatus
from app.models.user import User
from app.schemas.common import PageResult
from app.schemas.index_run import IndexRunCreate, IndexRunRead
from app.services.index_runs import IndexRunService
from app.services.task_queue import IndexTaskQueue

router = APIRouter()


def get_index_task_queue(request: Request) -> IndexTaskQueue:
    return request.app.state.index_task_queue


def get_admin_index_run_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    admin: Annotated[User, Depends(require_admin)],
) -> IndexRunService:
    return IndexRunService(session, created_by_id=admin.id)


def _serialize(run: Any) -> IndexRunRead:
    return IndexRunRead.model_validate(run)


async def _enqueue_run(
    *,
    run_id: int,
    service: IndexRunService,
    queue: IndexTaskQueue,
) -> Any:
    try:
        celery_task_id = await asyncio.to_thread(queue.enqueue, run_id)
    except Exception as exc:
        message = exc.message if hasattr(exc, "message") else "索引任务入队失败"
        error_code = str(exc.code) if hasattr(exc, "code") else "TASK_QUEUE_UNAVAILABLE"
        await service.fail(
            run_id,
            error_message=str(message),
            error_code=error_code,
        )
        raise
    return await service.set_celery_task_id(
        run_id,
        celery_task_id=celery_task_id,
    )


@router.get("", summary="List asynchronous index runs")
async def list_index_runs(
    service: Annotated[IndexRunService, Depends(get_admin_index_run_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[IndexRunStatus | None, Query()] = None,
    poem_version_id: int | None = None,
) -> Any:
    offset = (page - 1) * page_size
    runs, total = await service.list_runs(
        status=status,
        poem_version_id=poem_version_id,
        limit=page_size,
        offset=offset,
    )
    result = PageResult(
        items=[_serialize(run) for run in runs],
        total=total,
        page=page,
        page_size=page_size,
    )
    return success_response(
        [item.model_dump() for item in result.items],
        meta=result.meta.model_dump(),
    )


@router.post("", status_code=202, summary="Create and enqueue an index run")
async def create_index_run(
    payload: IndexRunCreate,
    service: Annotated[IndexRunService, Depends(get_admin_index_run_service)],
    queue: Annotated[IndexTaskQueue, Depends(get_index_task_queue)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", max_length=128),
    ] = None,
) -> Any:
    run = await service.create_run(
        payload.poem_version_id,
        config_snapshot={"rebuild_chunks": payload.rebuild_chunks},
        embedding_model=payload.embedding_model,
        embedding_dimension=payload.embedding_dimension,
        vector_collection=payload.vector_collection,
        chunk_strategy=payload.chunk_strategy,
        idempotency_key=idempotency_key,
        max_attempts=(
            payload.max_attempts
            if "max_attempts" in payload.model_fields_set
            else settings.index_task_max_attempts
        ),
    )
    if run.status == IndexRunStatus.PENDING.value and run.celery_task_id is None:
        run = await _enqueue_run(
            run_id=run.id,
            service=service,
            queue=queue,
        )
    return success_response(_serialize(run), status_code=202)


@router.get("/{run_id}", summary="Get an asynchronous index run")
async def get_index_run(
    run_id: int,
    service: Annotated[IndexRunService, Depends(get_admin_index_run_service)],
) -> Any:
    return success_response(_serialize(await service.get_run(run_id)))


@router.post("/{run_id}/retry", status_code=202, summary="Retry a failed index run")
async def retry_index_run(
    run_id: int,
    service: Annotated[IndexRunService, Depends(get_admin_index_run_service)],
    queue: Annotated[IndexTaskQueue, Depends(get_index_task_queue)],
) -> Any:
    run = await service.retry(run_id)
    run = await _enqueue_run(run_id=run.id, service=service, queue=queue)
    return success_response(_serialize(run), status_code=202)


@router.post("/{run_id}/cancel", summary="Cancel a pending or running index run")
async def cancel_index_run(
    run_id: int,
    service: Annotated[IndexRunService, Depends(get_admin_index_run_service)],
) -> Any:
    return success_response(_serialize(await service.cancel(run_id)))
