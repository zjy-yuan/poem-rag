from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.dynasty import Dynasty
    from app.models.poem import Poem


class Author(Base, TimestampMixin):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        String(120), unique=True, index=True, nullable=False
    )
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    dynasty_id: Mapped[int | None] = mapped_column(
        ForeignKey("dynasties.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    dynasty: Mapped[Dynasty | None] = relationship(back_populates="authors")
    poems: Mapped[list[Poem]] = relationship(back_populates="author")
