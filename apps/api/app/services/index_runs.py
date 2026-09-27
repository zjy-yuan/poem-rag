from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
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
_MAX_IDEMPOTENCY_KEY_LENGTH = 128
_MAX_WORKER_ID_LENGTH = 120
_MAX_LEASE_SECONDS = 3600


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
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> PoemIndexRun:
        if embedding_dimension is not None and embedding_dimension <= 0:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="embedding_dimension 必须大于 0",
            )
        if max_attempts < 1 or max_attempts > 10:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="max_attempts 必须在 1..10 范围内",
            )

        normalized_key = _normalize_idempotency_key(idempotency_key)
        if normalized_key is not None:
            existing = await self.session.scalar(
                select(PoemIndexRun).where(
                    PoemIndexRun.idempotency_key == normalized_key
                )
            )
            if existing is not None:
                return existing

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
            idempotency_key=normalized_key,
            max_attempts=max_attempts,
        )
        self.session.add(run)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            if normalized_key is not None:
                existing = await self.session.scalar(
                    select(PoemIndexRun).where(
                        PoemIndexRun.idempotency_key == normalized_key
                    )
                )
                if existing is not None:
                    return existing
            raise
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

    async def claim(
        self,
        run_id: int,
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> PoemIndexRun:
        normalized_worker = _normalize_worker_id(worker_id)
        _validate_lease_seconds(lease_seconds)
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(PoemIndexRun)
            .execution_options(synchronize_session=False)
            .where(
                PoemIndexRun.id == run_id,
                or_(
                    and_(
                        PoemIndexRun.status == IndexRunStatus.PENDING.value,
                        PoemIndexRun.lease_expires_at.is_(None),
                    ),
                    and_(
                        PoemIndexRun.status == IndexRunStatus.RUNNING.value,
                        PoemIndexRun.lease_expires_at.is_not(None),
                        PoemIndexRun.lease_expires_at <= now,
                    ),
                ),
                PoemIndexRun.cancel_requested_at.is_(None),
                PoemIndexRun.attempt_count < PoemIndexRun.max_attempts,
            )
            .values(
                status=IndexRunStatus.RUNNING.value,
                started_at=func.coalesce(PoemIndexRun.started_at, now),
                attempt_count=PoemIndexRun.attempt_count + 1,
                stage=IndexRunStage.CHUNK.value,
                chunk_count=0,
                embedded_count=0,
                lease_owner=normalized_worker,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                heartbeat_at=now,
                finished_at=None,
                error_code=None,
                error_message=None,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            run = await self._get_run(run_id)
            if run.attempt_count >= run.max_attempts:
                raise AppError(
                    status_code=409,
                    code=ErrorCode.INDEX_RUN_ATTEMPTS_EXHAUSTED,
                    message="索引任务已达到最大尝试次数",
                )
            raise AppError(
                status_code=409,
                code=ErrorCode.INDEX_RUN_INVALID_STATUS,
                message="索引任务不可领取",
                details=[
                    {
                        "field": "status",
                        "code": "current_status",
                        "message": run.status,
                    }
                ],
            )
        await self.session.commit()
        self.session.expire_all()
        return await self._get_run(run_id)

    async def heartbeat(
        self,
        run_id: int,
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> PoemIndexRun:
        normalized_worker = _normalize_worker_id(worker_id)
        _validate_lease_seconds(lease_seconds)
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(PoemIndexRun)
            .execution_options(synchronize_session=False)
            .where(
                PoemIndexRun.id == run_id,
                PoemIndexRun.status == IndexRunStatus.RUNNING.value,
                PoemIndexRun.lease_owner == normalized_worker,
                PoemIndexRun.lease_expires_at.is_not(None),
                PoemIndexRun.lease_expires_at > now,
                PoemIndexRun.cancel_requested_at.is_(None),
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise AppError(
                status_code=409,
                code=ErrorCode.INDEX_RUN_LEASE_LOST,
                message="索引任务租约已失效",
            )
        await self.session.commit()
        self.session.expire_all()
        return await self._get_run(run_id)

    async def release_for_retry(
        self,
        run_id: int,
        *,
        worker_id: str,
        error_code: str,
        error_message: str,
    ) -> PoemIndexRun:
        normalized_worker = _normalize_worker_id(worker_id)
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(PoemIndexRun)
            .execution_options(synchronize_session=False)
            .where(
                PoemIndexRun.id == run_id,
                PoemIndexRun.status == IndexRunStatus.RUNNING.value,
                PoemIndexRun.lease_owner == normalized_worker,
                PoemIndexRun.lease_expires_at.is_not(None),
                PoemIndexRun.lease_expires_at > now,
                PoemIndexRun.cancel_requested_at.is_(None),
                PoemIndexRun.attempt_count < PoemIndexRun.max_attempts,
            )
            .values(
                status=IndexRunStatus.PENDING.value,
                stage=IndexRunStage.CHUNK.value,
                chunk_count=0,
                embedded_count=0,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                finished_at=None,
                error_code=error_code,
                error_message=error_message[:2000],
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            run = await self._get_run(run_id)
            if run.attempt_count >= run.max_attempts:
                raise AppError(
                    status_code=409,
                    code=ErrorCode.INDEX_RUN_ATTEMPTS_EXHAUSTED,
                    message="索引任务已达到最大尝试次数",
                )
            raise AppError(
                status_code=409,
                code=ErrorCode.INDEX_RUN_LEASE_LOST,
                message="索引任务租约已失效",
            )
        await self.session.commit()
        self.session.expire_all()
        return await self._get_run(run_id)

    async def set_celery_task_id(
        self,
        run_id: int,
        *,
        celery_task_id: str,
    ) -> PoemIndexRun:
        normalized_task_id = celery_task_id.strip()
        if not normalized_task_id or len(normalized_task_id) > 155:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="celery_task_id 长度必须在 1..155 范围内",
            )
        run = await self._get_run(run_id)
        run.celery_task_id = normalized_task_id
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def get_run(self, run_id: int) -> PoemIndexRun:
        return await self._get_run(run_id)

    async def list_runs(
        self,
        *,
        status: IndexRunStatus | None = None,
        poem_version_id: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[PoemIndexRun], int]:
        if limit < 1 or limit > 100:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="limit 必须在 1..100 范围内",
            )
        if offset < 0:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="offset 不能小于 0",
            )

        conditions = []
        if status is not None:
            conditions.append(PoemIndexRun.status == status.value)
        if poem_version_id is not None:
            conditions.append(PoemIndexRun.poem_version_id == poem_version_id)

        total = await self.session.scalar(
            select(func.count()).select_from(PoemIndexRun).where(*conditions)
        )
        runs = list(
            (
                await self.session.execute(
                    select(PoemIndexRun)
                    .where(*conditions)
                    .order_by(PoemIndexRun.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        return runs, int(total or 0)

    async def list_recovery_candidates(
        self,
        *,
        now: datetime,
        pending_stale_before: datetime,
        limit: int = 100,
    ) -> list[PoemIndexRun]:
        if limit < 1 or limit > 1000:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="limit 必须在 1..1000 范围内",
            )

        runs = list(
            (
                await self.session.execute(
                    select(PoemIndexRun)
                    .where(
                        PoemIndexRun.cancel_requested_at.is_(None),
                        or_(
                            and_(
                                PoemIndexRun.status == IndexRunStatus.PENDING.value,
                                PoemIndexRun.updated_at <= pending_stale_before,
                            ),
                            and_(
                                PoemIndexRun.status == IndexRunStatus.RUNNING.value,
                                PoemIndexRun.lease_expires_at.is_not(None),
                                PoemIndexRun.lease_expires_at <= now,
                            ),
                        ),
                    )
                    .order_by(PoemIndexRun.id.asc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        return runs

    async def recover_expired_lease(
        self,
        run_id: int,
        *,
        now: datetime,
    ) -> PoemIndexRun:
        result = await self.session.execute(
            update(PoemIndexRun)
            .execution_options(synchronize_session=False)
            .where(
                PoemIndexRun.id == run_id,
                PoemIndexRun.status == IndexRunStatus.RUNNING.value,
                PoemIndexRun.lease_expires_at.is_not(None),
                PoemIndexRun.lease_expires_at <= now,
                PoemIndexRun.cancel_requested_at.is_(None),
                PoemIndexRun.attempt_count < PoemIndexRun.max_attempts,
            )
            .values(
                status=IndexRunStatus.PENDING.value,
                stage=IndexRunStage.CHUNK.value,
                chunk_count=0,
                embedded_count=0,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                finished_at=None,
                error_code=ErrorCode.INDEX_RUN_LEASE_EXPIRED.value,
                error_message="Worker 租约过期，任务已重新进入队列",
            )
        )
        if result.rowcount == 1:
            await self.session.commit()
            self.session.expire_all()
            return await self._get_run(run_id)

        result = await self.session.execute(
            update(PoemIndexRun)
            .execution_options(synchronize_session=False)
            .where(
                PoemIndexRun.id == run_id,
                PoemIndexRun.status == IndexRunStatus.RUNNING.value,
                PoemIndexRun.lease_expires_at.is_not(None),
                PoemIndexRun.lease_expires_at <= now,
                PoemIndexRun.cancel_requested_at.is_(None),
                PoemIndexRun.attempt_count >= PoemIndexRun.max_attempts,
            )
            .values(
                status=IndexRunStatus.FAILED.value,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                finished_at=now,
                error_code=ErrorCode.INDEX_RUN_LEASE_EXPIRED.value,
                error_message="Worker 租约过期且已达到最大尝试次数",
            )
        )
        if result.rowcount == 1:
            await self.session.commit()
            self.session.expire_all()
            return await self._get_run(run_id)

        await self.session.rollback()
        return await self._get_run(run_id)

    async def retry(self, run_id: int) -> PoemIndexRun:
        result = await self.session.execute(
            update(PoemIndexRun)
            .execution_options(synchronize_session=False)
            .where(
                PoemIndexRun.id == run_id,
                PoemIndexRun.status == IndexRunStatus.FAILED.value,
                PoemIndexRun.cancel_requested_at.is_(None),
                PoemIndexRun.attempt_count < PoemIndexRun.max_attempts,
            )
            .values(
                status=IndexRunStatus.PENDING.value,
                stage=IndexRunStage.CHUNK.value,
                chunk_count=0,
                embedded_count=0,
                lease_owner=None,
                lease_expires_at=None,
                heartbeat_at=None,
                finished_at=None,
                error_code=None,
                error_message=None,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            run = await self._get_run(run_id)
            if run.attempt_count >= run.max_attempts:
                raise AppError(
                    status_code=409,
                    code=ErrorCode.INDEX_RUN_ATTEMPTS_EXHAUSTED,
                    message="索引任务已达到最大尝试次数",
                )
            raise AppError(
                status_code=409,
                code=ErrorCode.INDEX_RUN_INVALID_STATUS,
                message="只有失败的索引任务可以重试",
            )
        await self.session.commit()
        self.session.expire_all()
        return await self._get_run(run_id)

    async def cancel(self, run_id: int) -> PoemIndexRun:
        now = datetime.now(UTC)
        result = await self.session.execute(
            update(PoemIndexRun)
            .execution_options(synchronize_session=False)
            .where(
                PoemIndexRun.id == run_id,
                PoemIndexRun.status.in_(
                    [
                        IndexRunStatus.PENDING.value,
                        IndexRunStatus.RUNNING.value,
                    ]
                ),
            )
            .values(
                status=IndexRunStatus.CANCELLED.value,
                cancel_requested_at=now,
                finished_at=now,
                lease_owner=None,
                lease_expires_at=None,
                error_code="INDEX_RUN_CANCELLED",
                error_message=None,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            run = await self._get_run(run_id)
            raise AppError(
                status_code=409,
                code=ErrorCode.INDEX_RUN_INVALID_STATUS,
                message="索引任务已经结束",
                details=[
                    {
                        "field": "status",
                        "code": "current_status",
                        "message": run.status,
                    }
                ],
            )
        await self.session.commit()
        self.session.expire_all()
        return await self._get_run(run_id)

    async def mark_chunks_ready(
        self,
        run_id: int,
        *,
        chunk_count: int,
        worker_id: str | None = None,
    ) -> PoemIndexRun:
        if chunk_count < 0:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="chunk_count 不能小于 0",
            )
        run = await self._get_writable_running_run(run_id, worker_id=worker_id)
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
        worker_id: str | None = None,
    ) -> PoemIndexRun:
        run = await self._get_writable_running_run(run_id, worker_id=worker_id)
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

    async def succeed(
        self,
        run_id: int,
        *,
        worker_id: str | None = None,
    ) -> PoemIndexRun:
        run = await self._get_writable_running_run(run_id, worker_id=worker_id)
        run.status = IndexRunStatus.SUCCEEDED.value
        run.finished_at = datetime.now(UTC)
        run.lease_owner = None
        run.lease_expires_at = None
        run.error_code = None
        run.error_message = None
        await self.session.commit()
        await self.session.refresh(run)
        return run

    async def fail(
        self,
        run_id: int,
        *,
        error_message: str,
        error_code: str | None = None,
        worker_id: str | None = None,
    ) -> PoemIndexRun:
        if worker_id is None:
            run = await self._get_run(run_id)
        else:
            run = await self._get_writable_running_run(run_id, worker_id=worker_id)
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
        run.error_code = error_code
        run.error_message = error_message[:2000]
        run.finished_at = datetime.now(UTC)
        run.lease_owner = None
        run.lease_expires_at = None
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

    async def _get_writable_running_run(
        self,
        run_id: int,
        *,
        worker_id: str | None,
    ) -> PoemIndexRun:
        if worker_id is None:
            return await self._get_running_run(run_id)

        normalized_worker = _normalize_worker_id(worker_id)
        now = datetime.now(UTC)
        run = await self.session.scalar(
            select(PoemIndexRun)
            .where(
                PoemIndexRun.id == run_id,
                PoemIndexRun.status == IndexRunStatus.RUNNING.value,
                PoemIndexRun.lease_owner == normalized_worker,
                PoemIndexRun.lease_expires_at.is_not(None),
                PoemIndexRun.lease_expires_at > now,
                PoemIndexRun.cancel_requested_at.is_(None),
            )
            .with_for_update()
        )
        if run is not None:
            return run

        existing = await self._get_run(run_id)
        if existing.status != IndexRunStatus.RUNNING.value:
            raise AppError(
                status_code=409,
                code=ErrorCode.INDEX_RUN_INVALID_STATUS,
                message="索引任务尚未开始或已经结束",
            )
        raise AppError(
            status_code=409,
            code=ErrorCode.INDEX_RUN_LEASE_LOST,
            message="索引任务租约已失效",
        )


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


def _normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > _MAX_IDEMPOTENCY_KEY_LENGTH:
        raise AppError(
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message="Idempotency-Key 过长",
        )
    return normalized


def _normalize_worker_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_WORKER_ID_LENGTH:
        raise AppError(
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message="worker_id 长度必须在 1..120 范围内",
        )
    return normalized


def _validate_lease_seconds(value: int) -> None:
    if value < 5 or value > _MAX_LEASE_SECONDS:
        raise AppError(
            status_code=422,
            code=ErrorCode.VALIDATION_ERROR,
            message="lease_seconds 必须在 5..3600 范围内",
        )
