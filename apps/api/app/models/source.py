from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.poem import Poem


class PoemSource(Base, TimestampMixin):
    __tablename__ = "poem_sources"
    __table_args__ = (
        UniqueConstraint(
            "source_key",
            "external_id",
            name="uq_poem_sources_source_key_external_id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poem_id: Mapped[int] = mapped_column(
        ForeignKey("poems.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(
        String(30),
        default="manual",
        server_default="manual",
        index=True,
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(
        String(100),
        default="manual",
        server_default="manual",
        nullable=False,
    )
    source_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    raw_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_author_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    raw_dynasty_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    license_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    poem: Mapped[Poem] = relationship(back_populates="sources")
