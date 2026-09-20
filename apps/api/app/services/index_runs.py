from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.index_run import IndexRunStage, IndexRunStatus, PoemIndexRun
from app.models.version import PoemVersion
from app.services.chunking import CHUNK_STRATEGY

_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
)


class IndexRunService:
    """Track a version's chunk, embedding and vector upsert lifecycle."""

    def __init__(self, session: AsyncSession, *, created_by_id: int | None = None) -> None:
        self.session = session
        self.created_by_id = created_by_id

    async def create_run(
        self,
        version_id: int,
        *,
        config_snapshot: dict[str, Any] | None = None,
        embedding_model: str | None = None,
        embedding_dimension: int | None = None,
        vector_collection: str | None = None,
        chunk_strategy: str = CHUNK_STRATEGY,
    ) -> PoemIndexRun:
        if embedding_dimension is not None and embedding_dimension <= 0:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="embedding_dimension 必须大于 0",
            )

        version = await self._get_version_for_update(version_id)
        active_run_id = await self.session.scalar(
            select(PoemIndexRun.id)
            .where(
                PoemIndexRun.poem_version_id == version.id,
                PoemIndexRun.status.in_(
                    [
                        IndexRunStatus.PENDING.value,
                        IndexRunStatus.RUNNING.value,
                    ]
                ),
            )
            .limit(1)
        )
        if active_run_id is not None:
            raise AppError(
                status_code=409,
                code=ErrorCode.INDEX_RUN_ALREADY_ACTIVE,
                message="该版本已有待执行或执行中的索引任务",
            )

        run = PoemIndexRun(
            poem_version_id=version.id,
            status=IndexRunStatus.PENDING.value,
            stage=IndexRunStage.CHUNK.value,
            chunk_strategy=chunk_strategy,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            vector_collection=vector_collection,
            config_snapshot=_sanitize_config_snapshot(config_snapshot or {}),
            created_by_id=self.created_by_id,
        )
        self.session.add(run)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def _get_version_for_update(self, version_id: int) -> PoemVersion:
        version = await self.session.scalar(
            select(PoemVersion).where(PoemVersion.id == version_id).with_for_update()
        )
        if version is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.POEM_VERSION_NOT_FOUND,
                message="诗词版本不存在",
            )
        return version

    async def start(self, run_id: int) -> PoemIndexRun:
        run = await self._get_run(run_id)
        if run.status != IndexRunStatus.PENDING.value:
            raise AppError(
                status_code=409,
                code=ErrorCode.INDEX_RUN_INVALID_STATUS,
                message="只有待执行的索引任务可以开始",
            )
        run.status = IndexRunStatus.RUNNING.value
        run.started_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def mark_chunks_ready(self, run_id: int, *, chunk_count: int) -> PoemIndexRun:
        if chunk_count < 0:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="chunk_count 不能小于 0",
            )
        run = await self._get_running_run(run_id)
        run.stage = IndexRunStage.EMBED.value
        run.chunk_count = chunk_count
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def mark_embeddings_ready(
        self,
        run_id: int,
        *,
        embedded_count: int,
        embedding_dimension: int | None = None,
    ) -> PoemIndexRun:
        run = await self._get_running_run(run_id)
        if embedded_count < 0 or embedded_count > run.chunk_count:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="embedded_count 超出有效范围",
            )
        if embedding_dimension is not None:
            if embedding_dimension <= 0:
                raise AppError(
                    status_code=422,
                    code=ErrorCode.VALIDATION_ERROR,
                    message="embedding_dimension 必须大于 0",
                )
            if (
                run.embedding_dimension is not None
                and run.embedding_dimension != embedding_dimension
            ):
                raise AppError(
                    status_code=422,
                    code=ErrorCode.VALIDATION_ERROR,
                    message="Embedding 实际维度与索引运行配置不一致",
                )
            run.embedding_dimension = embedding_dimension
        run.stage = IndexRunStage.UPSERT.value
        run.embedded_count = embedded_count
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def succeed(self, run_id: int) -> PoemIndexRun:
        run = await self._get_running_run(run_id)
        run.status = IndexRunStatus.SUCCEEDED.value
        run.finished_at = datetime.now(UTC)
        run.error_message = None
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def fail(self, run_id: int, *, error_message: str) -> PoemIndexRun:
        run = await self._get_run(run_id)
        if run.status not in {
            IndexRunStatus.PENDING.value,
            IndexRunStatus.RUNNING.value,
        }:
            raise AppError(
                status_code=409,
                code=ErrorCode.INDEX_RUN_INVALID_STATUS,
                message="索引任务已经结束",
            )
        run.status = IndexRunStatus.FAILED.value
        run.error_message = error_message[:2000]
        run.finished_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def _get_run(self, run_id: int) -> PoemIndexRun:
        run = await self.session.get(PoemIndexRun, run_id)
        if run is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.INDEX_RUN_NOT_FOUND,
                message="索引任务不存在",
            )
        return run

    async def _get_running_run(self, run_id: int) -> PoemIndexRun:
        run = await self._get_run(run_id)
        if run.status != IndexRunStatus.RUNNING.value:
            raise AppError(
                status_code=409,
                code=ErrorCode.INDEX_RUN_INVALID_STATUS,
                message="索引任务尚未开始或已经结束",
            )
        return run


def _sanitize_config_snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).casefold()
            if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = _sanitize_config_snapshot(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_config_snapshot(item) for item in value]
    return value
