from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.text import normalize_lookup
from app.models.annotation import AnnotationStatus, AnnotationType
from app.schemas.catalog import CategoryType


class CorpusImportSource(BaseModel):
    source_key: str = Field(min_length=1, max_length=100)
    source_type: Literal["file", "import", "crawl", "other"] = "file"
    source_name: str = Field(min_length=1, max_length=200)
    source_url: str | None = Field(default=None, max_length=2048)
    license_note: str = Field(min_length=1, max_length=500)

    @field_validator("source_key", "source_name", "license_note")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("导入来源字段不能为空")
        return normalized

    @field_validator("source_url")
    @classmethod
    def normalize_optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class CorpusImportDefaults(BaseModel):
    publish: bool = False


class CorpusImportCategory(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    type: CategoryType = CategoryType.WORK_TYPE

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("分类名称不能为空")
        return normalized


class CorpusImportAnnotation(BaseModel):
    type: AnnotationType = AnnotationType.APPRECIATION
    title: str | None = Field(default=None, max_length=255)
    content: str = Field(min_length=1, max_length=100000)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    status: AnnotationStatus = AnnotationStatus.PUBLISHED

    @field_validator("title", "content")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("注释内容不能为空")
        return normalized

    @model_validator(mode="after")
    def validate_line_range(self) -> CorpusImportAnnotation:
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("line_start 和 line_end 必须同时提供或同时为空")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end 不能小于 line_start")
        return self


class CorpusImportRecord(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=100000)
    author_name: str | None = Field(default=None, max_length=120)
    dynasty_name: str | None = Field(default=None, max_length=80)
    summary: str | None = Field(default=None, max_length=5000)
    source_url: str | None = Field(default=None, max_length=2048)
    publish: bool | None = None
    categories: list[CorpusImportCategory] = Field(default_factory=list, max_length=30)
    tags: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        default_factory=list,
        max_length=30,
    )
    annotations: list[CorpusImportAnnotation] = Field(default_factory=list, max_length=100)
    raw_payload: dict[str, Any] | None = None

    @field_validator("external_id", "title", "content")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("导入记录必填字段不能为空")
        return normalized

    @field_validator("author_name", "dynasty_name", "summary", "source_url")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("categories")
    @classmethod
    def deduplicate_categories(
        cls,
        values: list[CorpusImportCategory],
    ) -> list[CorpusImportCategory]:
        unique: list[CorpusImportCategory] = []
        seen: set[tuple[str, str]] = set()
        for value in values:
            key = (value.name.casefold(), value.type.value)
            if key in seen:
                continue
            seen.add(key)
            unique.append(value)
        return unique

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            tag = value.strip()
            lookup = normalize_lookup(tag)
            if not tag or lookup in seen:
                continue
            seen.add(lookup)
            normalized.append(tag)
        return normalized


class CorpusImportDataset(BaseModel):
    version: str = Field(min_length=1, max_length=100)
    source: CorpusImportSource
    defaults: CorpusImportDefaults = Field(default_factory=CorpusImportDefaults)
    records: list[CorpusImportRecord] = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def normalize_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("导入数据集版本不能为空")
        return normalized

    @model_validator(mode="after")
    def validate_unique_external_ids(self) -> CorpusImportDataset:
        external_ids = [record.external_id for record in self.records]
        if len(external_ids) != len(set(external_ids)):
            raise ValueError("同一导入数据集中的 external_id 必须唯一")
        return self


class CorpusImportRecordResult(BaseModel):
    external_id: str
    status: Literal["created", "updated", "unchanged", "failed"]
    poem_id: int | None = None
    version_id: int | None = None
    version_no: int | None = None
    message: str | None = None


class CorpusImportReport(BaseModel):
    dataset_version: str
    source_key: str
    total_records: int
    created_records: int
    updated_records: int
    unchanged_records: int
    failed_records: int
    records: list[CorpusImportRecordResult]
