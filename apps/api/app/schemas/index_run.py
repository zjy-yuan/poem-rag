from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.chunking import CHUNK_STRATEGY


class IndexRunCreate(BaseModel):
    poem_version_id: int = Field(gt=0)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=150)
    embedding_dimension: int | None = Field(default=None, gt=0)
    vector_collection: str | None = Field(default=None, min_length=1, max_length=150)
    chunk_strategy: str = Field(
        default=CHUNK_STRATEGY,
        min_length=1,
        max_length=100,
    )
    rebuild_chunks: bool = True
    max_attempts: int = Field(default=3, ge=1, le=10)


class IndexRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    poem_version_id: int
    status: str
    stage: str
    celery_task_id: str | None
    chunk_strategy: str
    embedding_model: str | None
    embedding_dimension: int | None
    vector_collection: str | None
    chunk_count: int
    embedded_count: int
    attempt_count: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    cancel_requested_at: datetime | None
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
