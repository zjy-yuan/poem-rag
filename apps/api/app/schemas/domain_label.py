from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.text import normalize_lookup
from app.models.domain_label import (
    DomainLabelDimension,
    DomainLabelGenerationMethod,
    DomainLabelReviewStatus,
    DomainLabelStatus,
)

DomainLabelAliasInput = Annotated[str, Field(min_length=1, max_length=80)]


class DomainLabelReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ARCHIVE = "archive"
    REASSESS = "reassess"


class DomainLabelAliasRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alias: str
    source_id: int | None
    created_at: datetime


class DomainLabelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dimension: DomainLabelDimension
    canonical_name: str
    normalized_name: str
    description: str | None
    status: DomainLabelStatus
    merged_into_id: int | None
    aliases: list[DomainLabelAliasRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class DomainLabelCreate(BaseModel):
    dimension: DomainLabelDimension
    canonical_name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    aliases: list[DomainLabelAliasInput] = Field(default_factory=list, max_length=50)

    @field_validator("canonical_name")
    @classmethod
    def normalize_canonical_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("标签名称不能为空")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        return _normalize_aliases(values)


class DomainLabelUpdate(BaseModel):
    canonical_name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    status: DomainLabelStatus | None = None
    merged_into_id: int | None = Field(default=None, ge=1)
    aliases: list[DomainLabelAliasInput] | None = Field(default=None, max_length=50)

    @field_validator("canonical_name")
    @classmethod
    def normalize_canonical_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("标签名称不能为空")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else _normalize_aliases(values)


class DomainLabelAssignmentCreate(BaseModel):
    domain_label_id: int = Field(ge=1)
    generation_method: DomainLabelGenerationMethod = DomainLabelGenerationMethod.MANUAL
    origin_ref: str | None = Field(default=None, max_length=120)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    evidence_text: str | None = Field(default=None, max_length=20000)
    line_start: int | None = Field(default=None, ge=0)
    line_end: int | None = Field(default=None, ge=0)
    model_name: str | None = Field(default=None, max_length=150)
    task_version: str | None = Field(default=None, max_length=100)

    @field_validator("origin_ref", "evidence_text", "model_name", "task_version")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_assignment_metadata(self) -> DomainLabelAssignmentCreate:
        if self.generation_method in {
            DomainLabelGenerationMethod.PUBLIC_DATASET,
            DomainLabelGenerationMethod.AI,
        } and self.origin_ref is None:
            raise ValueError("公开数据集或 AI 标签必须提供 origin_ref")
        if self.generation_method == DomainLabelGenerationMethod.AI and (
            self.model_name is None or self.task_version is None
        ):
            raise ValueError("AI 标签必须提供 model_name 和 task_version")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end 不能小于 line_start")
        return self


class DomainLabelAssignmentReviewRequest(BaseModel):
    action: DomainLabelReviewAction


class DomainLabelAssignmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    poem_id: int
    poem_version_id: int
    version_no: int
    label: DomainLabelRead
    generation_method: DomainLabelGenerationMethod
    origin_ref: str
    confidence: float | None
    review_status: DomainLabelReviewStatus
    evidence_text: str | None
    line_start: int | None
    line_end: int | None
    model_name: str | None
    task_version: str | None
    created_by_id: int | None
    reviewed_by_id: int | None
    reviewed_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PoemDomainLabelRead(BaseModel):
    label_id: int
    dimension: DomainLabelDimension
    canonical_name: str
    description: str | None
    generation_method: DomainLabelGenerationMethod
    evidence_text: str | None
    line_start: int | None
    line_end: int | None


def _normalize_aliases(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        alias = value.strip()
        normalized_key = normalize_lookup(alias)
        if not alias or not normalized_key or normalized_key in seen:
            continue
        seen.add(normalized_key)
        normalized.append(alias)
    return normalized
