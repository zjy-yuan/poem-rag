from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.domain_label_gold import DomainLabelGoldLabel


class DomainLabelReviewDecision(StrEnum):
    PENDING = "pending"
    APPROVE = "approve"
    NEEDS_CHANGE = "needs_change"
    REJECT = "reject"
    UNCERTAIN = "uncertain"


class DomainLabelReviewStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    READY_TO_FREEZE = "ready_to_freeze"


class DomainLabelLabelReview(BaseModel):
    """Review one original label while keeping the source item immutable."""

    review_id: str = Field(min_length=1, max_length=300)
    external_id: str = Field(min_length=1, max_length=255)
    original_label: DomainLabelGoldLabel
    decision: DomainLabelReviewDecision = DomainLabelReviewDecision.PENDING
    corrected_label: DomainLabelGoldLabel | None = None
    review_note: str | None = Field(default=None, max_length=1000)

    @field_validator("review_id", "external_id")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("标签复核必填字段不能为空")
        return normalized

    @field_validator("review_note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_decision(self) -> DomainLabelLabelReview:
        if self.decision == DomainLabelReviewDecision.NEEDS_CHANGE:
            if self.corrected_label is None:
                raise ValueError("needs_change 标签复核必须提供 corrected_label")
        elif self.corrected_label is not None:
            raise ValueError("只有 needs_change 标签复核可以修改 corrected_label")
        return self


class DomainLabelEmptyRecordReview(BaseModel):
    """Review a record-level conclusion that no controlled label is supported."""

    review_id: str = Field(min_length=1, max_length=300)
    external_id: str = Field(min_length=1, max_length=255)
    decision: DomainLabelReviewDecision = DomainLabelReviewDecision.PENDING
    corrected_labels: list[DomainLabelGoldLabel] = Field(
        default_factory=list,
        max_length=50,
    )
    review_note: str | None = Field(default=None, max_length=1000)

    @field_validator("review_id", "external_id")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("空标签复核必填字段不能为空")
        return normalized

    @field_validator("review_note")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_decision(self) -> DomainLabelEmptyRecordReview:
        if self.decision == DomainLabelReviewDecision.NEEDS_CHANGE:
            if not self.corrected_labels:
                raise ValueError(
                    "空标签结论标记为 needs_change 时必须提供 corrected_labels"
                )
        elif self.corrected_labels:
            raise ValueError(
                "只有 needs_change 空标签复核可以修改 corrected_labels"
            )
        return self


class DomainLabelGoldV2Review(BaseModel):
    version: str = Field(min_length=1, max_length=60)
    source_annotation_version: str = Field(min_length=1, max_length=60)
    source_annotation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_version: str = Field(min_length=1, max_length=60)
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: DomainLabelReviewStatus = DomainLabelReviewStatus.PENDING
    label_reviews: list[DomainLabelLabelReview] = Field(min_length=1)
    empty_record_reviews: list[DomainLabelEmptyRecordReview] = Field(
        default_factory=list
    )

    @field_validator(
        "version",
        "source_annotation_version",
        "source_manifest_version",
    )
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("独立复核必填字段不能为空")
        return normalized

    @model_validator(mode="after")
    def validate_coverage_keys(self) -> DomainLabelGoldV2Review:
        review_ids = [
            item.review_id
            for item in [*self.label_reviews, *self.empty_record_reviews]
        ]
        if len(review_ids) != len(set(review_ids)):
            raise ValueError("独立复核中的 review_id 必须唯一")

        empty_record_ids = [
            item.external_id for item in self.empty_record_reviews
        ]
        if len(empty_record_ids) != len(set(empty_record_ids)):
            raise ValueError("空标签复核中的 external_id 必须唯一")
        return self


class DomainLabelReviewValidationSummary(BaseModel):
    version: str
    status: DomainLabelReviewStatus
    label_review_count: int = Field(ge=0)
    empty_record_review_count: int = Field(ge=0)
    decided_label_review_count: int = Field(ge=0)
    decided_empty_record_review_count: int = Field(ge=0)
    pending_decision_count: int = Field(ge=0)
    approve_label_count: int = Field(ge=0)
    needs_change_label_count: int = Field(ge=0)
    reject_label_count: int = Field(ge=0)
    uncertain_label_count: int = Field(ge=0)
    approve_empty_record_count: int = Field(ge=0)
    needs_change_empty_record_count: int = Field(ge=0)
    reject_empty_record_count: int = Field(ge=0)
    uncertain_empty_record_count: int = Field(ge=0)
    blocking_review_ids: list[str]
    ready_to_freeze: bool
