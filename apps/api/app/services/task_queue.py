from __future__ import annotations

from typing import Protocol

from app.core.config import Settings
from app.core.errors import AppError, ErrorCode


class IndexTaskQueue(Protocol):
    def enqueue(self, run_id: int) -> str: ...


class CeleryIndexTaskQueue:
    def __init__(self, *, queue_name: str) -> None:
        self.queue_name = queue_name

    def enqueue(self, run_id: int) -> str:
        try:
            from app.tasks.indexing import index_poem_version

            result = index_poem_version.apply_async(
                args=[run_id],
                queue=self.queue_name,
                retry=False,
            )
        except Exception as exc:
            raise AppError(
                status_code=503,
                code=ErrorCode.TASK_QUEUE_UNAVAILABLE,
                message="索引任务队列暂时不可用",
            ) from exc
        return result.id


class DisabledIndexTaskQueue:
    def enqueue(self, run_id: int) -> str:
        del run_id
        raise AppError(
            status_code=503,
            code=ErrorCode.TASK_QUEUE_UNAVAILABLE,
            message="索引任务队列未启用",
        )


def create_index_task_queue(settings: Settings) -> IndexTaskQueue:
    if not settings.index_task_queue_enabled:
        return DisabledIndexTaskQueue()
    if not settings.effective_celery_broker_url:
        return DisabledIndexTaskQueue()
    return CeleryIndexTaskQueue(queue_name=settings.index_task_queue_name)
