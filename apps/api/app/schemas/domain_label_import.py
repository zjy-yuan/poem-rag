from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.text import normalize_lookup
from app.models.domain_label import (
    DomainLabelDimension,
    DomainLabelGenerationMethod,
)

MAX_ORIGIN_REF_LENGTH = 120


class DomainLabelImportSource(BaseModel):
    """Provenance of the label dataset itself."""

    source_key: str = Field(min_length=1, max_length=100)
    source_name: str = Field(min_length=1, max_length=200)
    source_url: str | None = Field(default=None, max_length=2048)
    license_note: str = Field(min_length=1, max_length=500)

    @field_validator("source_key", "source_name", "license_note")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("标签数据集来源字段不能为空")
        return normalized

    @field_validator("source_url")
    @classmethod
    def normalize_optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DomainLabelImportLabel(BaseModel):
    dimension: DomainLabelDimension
    name: str = Field(min_length=1, max_length=80)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    evidence_text: str | None = Field(default=None, max_length=20000)
    line_start: int | None = Field(default=None, ge=0)
    line_end: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("标签名称不能为空")
        return normalized

    @field_validator("evidence_text")
    @classmethod
    def normalize_evidence_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_line_range(self) -> DomainLabelImportLabel:
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end 不能小于 line_start")
        return self


class DomainLabelImportRecord(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
    labels: list[DomainLabelImportLabel] = Field(min_length=1, max_length=50)

    @field_validator("external_id")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("external_id 不能为空")
        return normalized

    @field_validator("labels")
    @classmethod
    def deduplicate_exact_labels(
        cls,
        values: list[DomainLabelImportLabel],
    ) -> list[DomainLabelImportLabel]:
        unique: list[DomainLabelImportLabel] = []
        seen: set[tuple[str, str]] = set()
        for value in values:
            key = (value.dimension.value, normalize_lookup(value.name))
            if key in seen:
                continue
            seen.add(key)
            unique.append(value)
        return unique


class DomainLabelImportDataset(BaseModel):
    version: str = Field(min_length=1, max_length=60)
    source: DomainLabelImportSource
    generation_method: DomainLabelGenerationMethod = DomainLabelGenerationMethod.MANUAL
    model_name: str | None = Field(default=None, max_length=150)
    task_version: str | None = Field(default=None, max_length=100)
    poem_source_key: str = Field(min_length=1, max_length=100)
    records: list[DomainLabelImportRecord] = Field(min_length=1)

    @field_validator("version", "poem_source_key")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("标签数据集必填字段不能为空")
        return normalized

    @field_validator("model_name", "task_version")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_dataset(self) -> DomainLabelImportDataset:
        if self.generation_method == DomainLabelGenerationMethod.AI and (
            self.model_name is None or self.task_version is None
        ):
            raise ValueError("AI 标签数据集必须提供 model_name 和 task_version")

        external_ids = [record.external_id for record in self.records]
        if len(external_ids) != len(set(external_ids)):
            raise ValueError("同一标签数据集中的 external_id 必须唯一")

        for record in self.records:
            for label in record.labels:
                origin_ref = build_domain_label_origin_ref(
                    generation_method=self.generation_method,
                    source_key=self.source.source_key,
                    dataset_version=self.version,
                    external_id=record.external_id,
                    dimension=label.dimension,
                    label_name=label.name,
                )
                if len(origin_ref) > MAX_ORIGIN_REF_LENGTH:
                    raise ValueError(
                        "origin_ref 超过数据库长度限制: "
                        f"external_id={record.external_id} name={label.name}"
                    )
        return self


class DomainLabelImportRecordResult(BaseModel):
    external_id: str
    status: Literal["created", "unchanged", "failed"]
    poem_id: int | None = None
    version_id: int | None = None
    version_no: int | None = None
    label_count: int = Field(ge=0)
    created_labels: int = Field(ge=0)
    unchanged_labels: int = Field(ge=0)
    failed_labels: int = Field(ge=0)
    message: str | None = None


class DomainLabelImportReport(BaseModel):
    dry_run: bool
    dataset_version: str
    source_key: str
    poem_source_key: str
    generation_method: DomainLabelGenerationMethod
    total_records: int = Field(ge=0)
    created_records: int = Field(ge=0)
    unchanged_records: int = Field(ge=0)
    failed_records: int = Field(ge=0)
    created_assignments: int = Field(ge=0)
    unchanged_assignments: int = Field(ge=0)
    failed_assignments: int = Field(ge=0)
    records: list[DomainLabelImportRecordResult]


def build_domain_label_origin_ref(
    *,
    generation_method: DomainLabelGenerationMethod,
    source_key: str,
    dataset_version: str,
    external_id: str,
    dimension: DomainLabelDimension,
    label_name: str,
) -> str:
    return ":".join(
        [
            generation_method.value,
            source_key,
            dataset_version,
            external_id,
            dimension.value,
            normalize_lookup(label_name),
        ]
    )
