from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.author import Author
    from app.models.category import Category
    from app.models.dynasty import Dynasty
    from app.models.source import PoemSource
    from app.models.tag import PoemTag
    from app.models.version import PoemVersion


class PoemStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Poem(Base, TimestampMixin):
    __tablename__ = "poems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("authors.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    dynasty_id: Mapped[int | None] = mapped_column(
        ForeignKey("dynasties.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        default=PoemStatus.DRAFT.value,
        server_default=PoemStatus.DRAFT.value,
        index=True,
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )

    author: Mapped[Author | None] = relationship(back_populates="poems")
    dynasty: Mapped[Dynasty | None] = relationship(back_populates="poems")
    category_links: Mapped[list[PoemCategory]] = relationship(
        back_populates="poem",
        cascade="all, delete-orphan",
    )
    tag_links: Mapped[list[PoemTag]] = relationship(
        back_populates="poem",
        cascade="all, delete-orphan",
    )
    sources: Mapped[list[PoemSource]] = relationship(
        back_populates="poem",
        cascade="all, delete-orphan",
    )
    versions: Mapped[list[PoemVersion]] = relationship(
        back_populates="poem",
        cascade="all, delete-orphan",
    )


class PoemCategory(Base):
    __tablename__ = "poem_categories"

    poem_id: Mapped[int] = mapped_column(
        ForeignKey("poems.id", ondelete="CASCADE"),
        primary_key=True,
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source: Mapped[str] = mapped_column(
        String(30),
        default="manual",
        server_default="manual",
        nullable=False,
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
        nullable=False,
    )

    poem: Mapped[Poem] = relationship(back_populates="category_links")
    category: Mapped[Category] = relationship(back_populates="poem_links")
