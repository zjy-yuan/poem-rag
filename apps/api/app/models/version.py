from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.annotation import PoemAnnotation
    from app.models.chunk import PoemChunk
    from app.models.index_run import PoemIndexRun
    from app.models.poem import Poem
    from app.models.source import PoemSource


class PoemVersionChangeType(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    ARCHIVE = "archive"
    RESTORE = "restore"
    IMPORT = "import"
    BACKFILL = "backfill"


class PoemVersion(Base):
    __tablename__ = "poem_versions"
    __table_args__ = (
        UniqueConstraint("poem_id", "version_no", name="uq_poem_versions_poem_id_version_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poem_id: Mapped[int] = mapped_column(
        ForeignKey("poems.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("poem_sources.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    change_type: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    changed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    change_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    poem: Mapped[Poem] = relationship(back_populates="versions")
    source: Mapped[PoemSource | None] = relationship()
    annotations: Mapped[list[PoemAnnotation]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
    )
    chunks: Mapped[list[PoemChunk]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
    )
    index_runs: Mapped[list[PoemIndexRun]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
    )
