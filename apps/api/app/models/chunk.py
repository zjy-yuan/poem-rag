from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.annotation import PoemAnnotation
    from app.models.version import PoemVersion


class ChunkGranularity(StrEnum):
    POEM = "poem"
    LINE = "line"
    NOTE = "note"


class ChunkStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    DISABLED = "disabled"


class PoemChunk(Base, TimestampMixin):
    __tablename__ = "poem_chunks"
    __table_args__ = (
        UniqueConstraint(
            "poem_version_id",
            "granularity",
            "chunk_strategy",
            "chunk_index",
            name="uq_poem_chunks_version_granularity_strategy_index",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poem_id: Mapped[int] = mapped_column(
        ForeignKey("poems.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    poem_version_id: Mapped[int] = mapped_column(
        ForeignKey("poem_versions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    annotation_id: Mapped[int | None] = mapped_column(
        ForeignKey("poem_annotations.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    granularity: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vector_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_strategy: Mapped[str] = mapped_column(
        String(100),
        default="structural-v1",
        server_default="structural-v1",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=ChunkStatus.PENDING.value,
        server_default=ChunkStatus.PENDING.value,
        index=True,
        nullable=False,
    )

    version: Mapped[PoemVersion] = relationship(back_populates="chunks")
    annotation: Mapped[PoemAnnotation | None] = relationship(back_populates="chunks")
