from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.message import MessageRole, MessageStatus


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("会话标题不能为空")
        return value.strip()


class ConversationUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("会话标题不能为空")
        return value.strip()


class ConversationRead(BaseModel):
    id: int
    title: str
    status: str
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MessageCitationRead(BaseModel):
    id: int
    chunk_id: int | None
    poem_id: int | None
    poem_version_id: int | None
    annotation_id: int | None
    title: str
    author_name: str | None
    dynasty_name: str | None
    granularity: str
    text: str
    score: float
    rank: int


class MessageRead(BaseModel):
    id: int
    conversation_id: int
    role: MessageRole
    content: str
    status: MessageStatus
    model: str | None
    latency_ms: int | None
    error_code: str | None
    citations: list[MessageCitationRead]
    created_at: datetime
    updated_at: datetime


class ChatStreamRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    model: str | None = Field(default=None, max_length=150)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息内容不能为空")
        return value.strip()
