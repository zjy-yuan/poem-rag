from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.domain_label_gold import DomainLabelGoldLabel


class DomainLabelAnnotationDraftStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    READY_FOR_REVIEW = "ready_for_review"


class DomainLabelAnnotationRecordStatus(StrEnum):
    PENDING = "pending"
    REVIEWED = "reviewed"


class DomainLabelAnnotationRecord(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
    annotation_status: DomainLabelAnnotationRecordStatus = (
        DomainLabelAnnotationRecordStatus.PENDING
    )
    labels: list[DomainLabelGoldLabel] = Field(default_factory=list, max_length=50)
    review_note: str | None = Field(default=None, max_length=1000)

    @field_validator("external_id")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("标注记录 external_id 不能为空")
        return normalized

    @field_validator("review_note")
    @classmethod
    def normalize_review_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("labels")
    @classmethod
    def deduplicate_labels(
        cls,
        values: list[DomainLabelGoldLabel],
    ) -> list[DomainLabelGoldLabel]:
        seen: set[tuple[str, str]] = set()
        for value in values:
            if value.lookup_key in seen:
                raise ValueError(
                    "同一标注记录内不能重复标签: "
                    f"dimension={value.dimension.value} name={value.name}"
                )
            seen.add(value.lookup_key)
        return values


class DomainLabelAnnotationDraft(BaseModel):
    version: str = Field(min_length=1, max_length=60)
    source_manifest_version: str = Field(min_length=1, max_length=60)
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_generated_at: datetime
    status: DomainLabelAnnotationDraftStatus = (
        DomainLabelAnnotationDraftStatus.PENDING
    )
    records: list[DomainLabelAnnotationRecord] = Field(min_length=1)

    @field_validator("version", "source_manifest_version")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("标注草稿必填字段不能为空")
        return normalized

    @model_validator(mode="after")
    def validate_draft(self) -> DomainLabelAnnotationDraft:
        external_ids = [record.external_id for record in self.records]
        if len(external_ids) != len(set(external_ids)):
            raise ValueError("标注草稿中的 external_id 必须唯一")
        if self.status == DomainLabelAnnotationDraftStatus.READY_FOR_REVIEW:
            pending_ids = [
                record.external_id
                for record in self.records
                if record.annotation_status
                != DomainLabelAnnotationRecordStatus.REVIEWED
            ]
            if pending_ids:
                raise ValueError(
                    "标注草稿标记为 ready_for_review 时不能存在 pending 记录: "
                    + ", ".join(pending_ids)
                )
            if not any(record.labels for record in self.records):
                raise ValueError("标注草稿至少需要一条标签")
        return self


class DomainLabelAnnotationValidationSummary(BaseModel):
    record_count: int = Field(ge=0)
    reviewed_record_count: int = Field(ge=0)
    pending_record_count: int = Field(ge=0)
    label_count: int = Field(ge=0)
    critical_label_count: int = Field(ge=0)
    dimension_counts: dict[str, int]
    pending_external_ids: list[str]
