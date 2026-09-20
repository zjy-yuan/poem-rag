from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.chunk import ChunkGranularity


class RetrievalEvidence(BaseModel):
    chunk_id: int
    poem_id: int
    poem_version_id: int
    annotation_id: int | None
    annotation_type: str | None
    title: str
    author_id: int | None
    author_name: str | None
    dynasty_id: int | None
    dynasty_name: str | None
    granularity: ChunkGranularity
    chunk_index: int
    text: str
    line_start: int | None
    line_end: int | None
    chunk_strategy: str
    status: str
    score: float = Field(ge=0, le=1)
    match_types: list[str]
    published_at: datetime | None
