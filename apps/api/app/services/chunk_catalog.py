from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ErrorCode
from app.models.annotation import AnnotationStatus, PoemAnnotation
from app.models.chunk import ChunkStatus, PoemChunk
from app.models.version import PoemVersion
from app.services.chunking import (
    CHUNK_STRATEGY,
    AnnotationChunkInput,
    ChunkDraft,
    chunk_poem,
)


class ChunkCatalogService:
    """Rebuild pending chunks for an immutable poem version."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def rebuild_version_chunks(self, version_id: int) -> list[PoemChunk]:
        version = await self.session.get(PoemVersion, version_id)
        if version is None:
            raise AppError(
                status_code=404,
                code=ErrorCode.POEM_VERSION_NOT_FOUND,
                message="诗词版本不存在",
            )

        existing = list(
            (
                await self.session.execute(
                    select(PoemChunk).where(PoemChunk.poem_version_id == version_id)
                )
            )
            .scalars()
            .all()
        )
        if any(chunk.vector_id is not None for chunk in existing):
            raise AppError(
                status_code=409,
                code=ErrorCode.CHUNKS_ALREADY_INDEXED,
                message="该版本已有向量索引，需先执行索引清理",
            )

        annotations = await self._published_annotations(version_id)
        content = self._snapshot_content(version)
        try:
            drafts = chunk_poem(
                content,
                annotations=[
                    AnnotationChunkInput(
                        annotation_id=annotation.id,
                        content=annotation.content,
                        line_start=annotation.line_start,
                        line_end=annotation.line_end,
                    )
                    for annotation in annotations
                ],
            )
        except ValueError as exc:
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message=str(exc),
            ) from exc

        if existing:
            await self.session.execute(
                delete(PoemChunk).where(PoemChunk.poem_version_id == version_id)
            )

        chunks = [
            self._to_model(version, draft)
            for draft in drafts
        ]
        self.session.add_all(chunks)
        await self.session.commit()
        for chunk in chunks:
            await self.session.refresh(chunk)
        return chunks

    async def _published_annotations(self, version_id: int) -> list[PoemAnnotation]:
        result = await self.session.execute(
            select(PoemAnnotation)
            .where(
                PoemAnnotation.poem_version_id == version_id,
                PoemAnnotation.status == AnnotationStatus.PUBLISHED.value,
            )
            .order_by(PoemAnnotation.id)
        )
        return list(result.scalars())

    @staticmethod
    def _snapshot_content(version: PoemVersion) -> str:
        content = version.snapshot.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AppError(
                status_code=422,
                code=ErrorCode.VALIDATION_ERROR,
                message="版本快照缺少可切分的正文",
            )
        return content

    @staticmethod
    def _to_model(
        version: PoemVersion,
        draft: ChunkDraft,
    ) -> PoemChunk:
        return PoemChunk(
            poem_id=version.poem_id,
            poem_version_id=version.id,
            annotation_id=draft.annotation_id,
            granularity=draft.granularity.value,
            chunk_index=draft.chunk_index,
            text=draft.text,
            normalized_text=draft.normalized_text,
            content_hash=draft.content_hash,
            line_start=draft.line_start,
            line_end=draft.line_end,
            chunk_strategy=CHUNK_STRATEGY,
            status=ChunkStatus.PENDING.value,
        )
