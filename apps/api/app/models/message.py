from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.chunk import PoemChunk
    from app.models.conversation import Conversation


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MessageStatus.COMPLETED.value,
        server_default=MessageStatus.COMPLETED.value,
        index=True,
        nullable=False,
    )
    model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    citations: Mapped[list[MessageCitation]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="MessageCitation.rank",
    )


class MessageCitation(Base, TimestampMixin):
    __tablename__ = "message_citations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    chunk_id: Mapped[int | None] = mapped_column(
        ForeignKey("poem_chunks.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    poem_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poem_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    annotation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    author_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    dynasty_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    granularity: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    message: Mapped[Message] = relationship(back_populates="citations")
    chunk: Mapped[PoemChunk | None] = relationship()
