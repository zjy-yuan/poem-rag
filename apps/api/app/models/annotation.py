from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.chunk import PoemChunk
    from app.models.version import PoemVersion


class AnnotationType(StrEnum):
    NOTE = "note"
    TRANSLATION = "translation"
    APPRECIATION = "appreciation"
    BACKGROUND = "background"
    ALLUSION = "allusion"
    OTHER = "other"


class AnnotationStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class PoemAnnotation(Base, TimestampMixin):
    __tablename__ = "poem_annotations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poem_version_id: Mapped[int] = mapped_column(
        ForeignKey("poem_versions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("poem_sources.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    annotation_type: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content: Mapped[str] = mapped_column(Text, nullable=False)
    line_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=AnnotationStatus.DRAFT.value,
        server_default=AnnotationStatus.DRAFT.value,
        index=True,
        nullable=False,
    )
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    version: Mapped[PoemVersion] = relationship(back_populates="annotations")
    chunks: Mapped[list[PoemChunk]] = relationship(back_populates="annotation")
