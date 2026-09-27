from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from app.api.v1.admin.index_runs import _enqueue_run
from app.core.errors import AppError, ErrorCode
from app.services.task_queue import CeleryIndexTaskQueue
from app.tasks.celery_app import celery_app


def test_celery_app_disables_publish_retries() -> None:
    assert celery_app.conf.task_publish_retry is False
    assert celery_app.conf.broker_connection_retry is True
    assert celery_app.conf.broker_connection_retry_on_startup is True
    assert celery_app.conf.broker_transport_options["max_retries"] == 0


def test_celery_queue_disables_publish_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Result:
        id = "celery-task-1"

    def fake_apply_async(*args: Any, **kwargs: Any) -> Result:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Result()

    monkeypatch.setattr(
        "app.tasks.indexing.index_poem_version.apply_async",
        fake_apply_async,
    )

    result = CeleryIndexTaskQueue(queue_name="poem.index").enqueue(7)

    assert result == "celery-task-1"
    assert captured["args"] == ()
    assert captured["kwargs"] == {
        "args": [7],
        "queue": "poem.index",
        "retry": False,
    }


def test_celery_queue_maps_publish_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_apply_async(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise OSError("broker unavailable")

    monkeypatch.setattr(
        "app.tasks.indexing.index_poem_version.apply_async",
        fail_apply_async,
    )

    with pytest.raises(AppError) as exc_info:
        CeleryIndexTaskQueue(queue_name="poem.index").enqueue(7)

    assert exc_info.value.status_code == 503
    assert exc_info.value.code == ErrorCode.TASK_QUEUE_UNAVAILABLE


def test_enqueue_run_offloads_blocking_publish_to_worker_thread() -> None:
    main_thread_id = threading.get_ident()

    class SlowQueue:
        def __init__(self) -> None:
            self.thread_id: int | None = None

        def enqueue(self, run_id: int) -> str:
            del run_id
            self.thread_id = threading.get_ident()
            return "celery-task-1"

    class FakeService:
        async def set_celery_task_id(self, run_id: int, *, celery_task_id: str) -> Any:
            return {
                "run_id": run_id,
                "celery_task_id": celery_task_id,
            }

    queue = SlowQueue()

    async def enqueue() -> Any:
        return await _enqueue_run(
            run_id=7,
            service=FakeService(),  # type: ignore[arg-type]
            queue=queue,  # type: ignore[arg-type]
        )

    result = asyncio.run(enqueue())

    assert result == {"run_id": 7, "celery_task_id": "celery-task-1"}
    assert queue.thread_id is not None
    assert queue.thread_id != main_thread_id
