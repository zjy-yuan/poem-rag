from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.version import PoemVersion


class IndexRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IndexRunStage(StrEnum):
    CHUNK = "chunk"
    EMBED = "embed"
    UPSERT = "upsert"


class PoemIndexRun(Base, TimestampMixin):
    __tablename__ = "poem_index_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poem_version_id: Mapped[int] = mapped_column(
        ForeignKey("poem_versions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default=IndexRunStatus.PENDING.value,
        server_default=IndexRunStatus.PENDING.value,
        index=True,
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(
        String(20),
        default=IndexRunStage.CHUNK.value,
        server_default=IndexRunStage.CHUNK.value,
        nullable=False,
    )
    chunk_strategy: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vector_collection: Mapped[str | None] = mapped_column(String(150), nullable=True)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    chunk_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    embedded_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    version: Mapped[PoemVersion] = relationship(back_populates="index_runs")
    created_by: Mapped[User | None] = relationship()
