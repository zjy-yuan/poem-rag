from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.poem import PoemStatus


class CategoryType(StrEnum):
    WORK_TYPE = "work_type"
    FORM = "form"
    STYLE = "style"
    THEME = "theme"


class DynastyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    sort_order: int
    created_at: datetime
    updated_at: datetime


class DynastyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    sort_order: int = Field(default=0, ge=0, le=100000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("朝代名称不能为空")
        return normalized


class DynastyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    sort_order: int | None = Field(default=None, ge=0, le=100000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("朝代名称不能为空")
        return normalized


class AuthorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    aliases: list[str]
    bio: str | None
    dynasty_id: int | None
    dynasty_name: str | None = None
    poem_count: int = 0
    created_at: datetime
    updated_at: datetime


class AuthorDetail(AuthorRead):
    poems: list[PoemSummary] = Field(default_factory=list)


class AuthorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    bio: str | None = Field(default=None, max_length=5000)
    dynasty_id: int | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("作者名称不能为空")
        return normalized

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        return _normalize_labels(values)


class AuthorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    aliases: list[str] | None = Field(default=None, max_length=30)
    bio: str | None = Field(default=None, max_length=5000)
    dynasty_id: int | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("作者名称不能为空")
        return normalized

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else _normalize_labels(values)


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: CategoryType
    parent_id: int | None
    sort_order: int
    is_active: bool
    poem_count: int = 0
    created_at: datetime
    updated_at: datetime


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    type: CategoryType
    parent_id: int | None = None
    sort_order: int = Field(default=0, ge=0, le=100000)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("分类名称不能为空")
        return normalized


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    type: CategoryType | None = None
    parent_id: int | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100000)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("分类名称不能为空")
        return normalized


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class PoemSummary(BaseModel):
    id: int
    title: str
    summary: str | None
    status: PoemStatus
    published_at: datetime | None


class PoemRead(BaseModel):
    id: int
    title: str
    author_id: int | None
    author_name: str | None
    dynasty_id: int | None
    dynasty_name: str | None
    content: str
    summary: str | None
    status: PoemStatus
    version_no: int
    published_at: datetime | None
    deleted_at: datetime | None
    categories: list[CategoryRead] = Field(default_factory=list)
    tags: list[TagRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PoemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    author_id: int | None = None
    dynasty_id: int | None = None
    content: str = Field(min_length=1, max_length=100000)
    summary: str | None = Field(default=None, max_length=5000)
    category_ids: list[int] = Field(default_factory=list, max_length=30)
    tag_names: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title", "content")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("内容不能为空")
        return normalized

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("category_ids")
    @classmethod
    def deduplicate_category_ids(cls, values: list[int]) -> list[int]:
        return list(dict.fromkeys(values))

    @field_validator("tag_names")
    @classmethod
    def normalize_tag_names(cls, values: list[str]) -> list[str]:
        return _normalize_labels(values)


class PoemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    author_id: int | None = None
    dynasty_id: int | None = None
    content: str | None = Field(default=None, min_length=1, max_length=100000)
    summary: str | None = Field(default=None, max_length=5000)
    category_ids: list[int] | None = Field(default=None, max_length=30)
    tag_names: list[str] | None = Field(default=None, max_length=30)
    version_no: int = Field(ge=1)

    @field_validator("title", "content")
    @classmethod
    def normalize_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("内容不能为空")
        return normalized

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("category_ids")
    @classmethod
    def deduplicate_category_ids(cls, values: list[int] | None) -> list[int] | None:
        return None if values is None else list(dict.fromkeys(values))

    @field_validator("tag_names")
    @classmethod
    def normalize_tag_names(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else _normalize_labels(values)


def _normalize_labels(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        label = value.strip()
        if not label or label in seen:
            continue
        seen.add(label)
        normalized.append(label)
    return normalized
