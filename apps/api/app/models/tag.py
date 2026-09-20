from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.poem import Poem


class Tag(Base, TimestampMixin):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String(80), unique=True, index=True, nullable=False
    )

    poem_links: Mapped[list[PoemTag]] = relationship(
        back_populates="tag",
        cascade="all, delete-orphan",
    )


class PoemTag(Base):
    __tablename__ = "poem_tags"

    poem_id: Mapped[int] = mapped_column(
        ForeignKey("poems.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="0",
        nullable=False,
    )

    poem: Mapped[Poem] = relationship(back_populates="tag_links")
    tag: Mapped[Tag] = relationship(back_populates="poem_links")
