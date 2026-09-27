from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "poem_rag",
    broker=settings.effective_celery_broker_url or "memory://",
    include=["app.tasks.indexing", "app.tasks.reconciliation"],
)
celery_app.conf.update(
    task_publish_retry=False,
    task_default_queue=settings.index_task_queue_name,
    task_routes={
        "app.tasks.indexing.index_poem_version": {
            "queue": settings.index_task_queue_name,
        }
    },
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "socket_connect_timeout": settings.index_task_broker_socket_timeout_seconds,
        "socket_timeout": settings.index_task_broker_socket_timeout_seconds,
        "retry_on_timeout": False,
        "max_retries": 0,
        "interval_start": 0,
        "interval_step": 0,
        "interval_max": 0,
    },
)

if settings.index_task_reconcile_enabled:
    celery_app.conf.beat_schedule = {
        "reconcile-stale-index-runs": {
            "task": "app.tasks.reconciliation.reconcile_index_runs",
            "schedule": settings.index_task_reconcile_interval_seconds,
            "options": {"queue": settings.index_task_queue_name},
        }
    }
